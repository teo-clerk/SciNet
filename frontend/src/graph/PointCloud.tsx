/**
 * The node renderer: one THREE.Points, one draw call.
 *
 * Everything per-node is a BufferAttribute. Colour mode, filtering, hover and
 * selection all write into those arrays and flag `needsUpdate` — no geometry
 * rebuilds, no re-renders, no allocation on the hot path.
 */
import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'

import fragmentShader from './shaders/point.frag.glsl?raw'
import vertexShader from './shaders/point.vert.glsl?raw'
import {
  CLUSTER_PALETTE,
  NOISE_COLOR,
  PROVISIONAL_COLOR,
  SETTLED_COLOR,
  useGraphStore,
  yearColor,
} from '@/state/graphStore'
import { useVisibleSet } from '@/lib/filtering'

const FOG_COLOR = new THREE.Color('#050510')
export const PICK_LAYER = 1

export function PointCloud() {
  const buffers = useGraphStore((s) => s.buffers)
  const nodes = useGraphStore((s) => s.nodes)
  const colorMode = useGraphStore((s) => s.colorMode)
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const hoveredIndex = useGraphStore((s) => s.hoveredIndex)
  const visible = useVisibleSet()

  const geometryRef = useRef<THREE.BufferGeometry>(null)
  const { gl } = useThree()

  const geometry = useMemo(() => {
    if (!buffers) return null
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(buffers.positions, 3))
    g.setAttribute('aColor', new THREE.BufferAttribute(buffers.colors, 3))
    g.setAttribute('aSize', new THREE.BufferAttribute(buffers.sizes, 1))
    g.setAttribute('aFiltered', new THREE.BufferAttribute(buffers.filtered, 1))
    g.setAttribute('aSelected', new THREE.BufferAttribute(buffers.selected, 1))

    // Node index encoded as a colour, for GPU picking. Built once: identity
    // does not change, only appearance does.
    const pick = new Float32Array(nodes.length * 3)
    for (let i = 0; i < nodes.length; i++) {
      pick[i * 3] = ((i + 1) & 0xff) / 255
      pick[i * 3 + 1] = (((i + 1) >> 8) & 0xff) / 255
      pick[i * 3 + 2] = (((i + 1) >> 16) & 0xff) / 255
    }
    g.setAttribute('aPickColor', new THREE.BufferAttribute(pick, 3))
    g.computeBoundingSphere()
    return g
  }, [buffers, nodes.length])

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
          uPixelRatio: { value: Math.min(gl.getPixelRatio(), 2) },
          uBaseSize: { value: 3.4 },
          uFogColor: { value: FOG_COLOR },
          uFogNear: { value: 70.0 },
          uFogFar: { value: 260.0 },
          uIntensity: { value: 0.42 },
        },
        transparent: true,
        // Additive: overlapping sprites sum instead of occluding, so a dense
        // cluster brightens into a glow while a sparse region stays dim. The
        // density of the map becomes visible rather than merely inferred.
        blending: THREE.AdditiveBlending,
        // depthWrite off deliberately: soft sprites that write depth clip each
        // other at the rim, which reads as a dark halo around every node. With
        // additive blending it would also make the result order-dependent.
        depthWrite: false,
        depthTest: true,
      }),
    [gl],
  )

  // --- colour mode: rewrite the colour attribute, nothing else -----------
  useEffect(() => {
    const g = geometryRef.current
    if (!g || !buffers || nodes.length === 0) return

    const attr = g.getAttribute('aColor') as THREE.BufferAttribute
    const colors = attr.array as Float32Array
    const sizes = g.getAttribute('aSize') as THREE.BufferAttribute
    const sizeArr = sizes.array as Float32Array

    // A loop rather than Math.min(...years): spreading an array past ~65k
    // elements exceeds the argument limit and throws.
    let minYear = Number.POSITIVE_INFINITY
    let maxYear = Number.NEGATIVE_INFINITY
    for (const node of nodes) {
      if (node.year === null) continue
      if (node.year < minYear) minYear = node.year
      if (node.year > maxYear) maxYear = node.year
    }
    if (!Number.isFinite(minYear)) {
      minYear = 0
      maxYear = 1
    }

    nodes.forEach((node, i) => {
      let rgb: [number, number, number]
      if (colorMode === 'cluster') {
        rgb =
          node.cluster === null
            ? NOISE_COLOR
            : CLUSTER_PALETTE[node.cluster % CLUSTER_PALETTE.length]!
      } else if (colorMode === 'year') {
        rgb = yearColor(node.year, minYear, maxYear)
      } else {
        rgb = node.provisional ? PROVISIONAL_COLOR : SETTLED_COLOR
      }
      colors[i * 3] = rgb[0]
      colors[i * 3 + 1] = rgb[1]
      colors[i * 3 + 2] = rgb[2]

      // Size carries page count, log-scaled: the corpus spans 2 to 160 pages,
      // and a linear map would make one review article dwarf every letter on
      // the map. Log keeps a substantial paper visibly larger without letting
      // the extremes dominate.
      const pages = node.pages ?? 12
      const magnitude = Math.log(Math.max(pages, 1) + 1) / Math.log(21) // 20pp -> 1.0
      sizeArr[i] =
        colorMode === 'provisional' && node.provisional
          ? 1 + Math.min(node.drift, 3) * 0.35
          : 0.62 + 0.78 * Math.min(magnitude, 1.9)
    })
    attr.needsUpdate = true
    sizes.needsUpdate = true
  }, [colorMode, nodes, buffers])

  // --- filtering: an attribute write, never a geometry rebuild -----------
  useEffect(() => {
    const g = geometryRef.current
    if (!g || !buffers) return
    const attr = g.getAttribute('aFiltered') as THREE.BufferAttribute
    const arr = attr.array as Float32Array
    if (visible === null) {
      arr.fill(1)
    } else {
      for (let i = 0; i < arr.length; i++) arr[i] = visible.has(i) ? 1 : 0
    }
    attr.needsUpdate = true
  }, [visible, buffers])

  // --- hover / selection --------------------------------------------------
  useEffect(() => {
    const g = geometryRef.current
    if (!g || !buffers) return
    const attr = g.getAttribute('aSelected') as THREE.BufferAttribute
    const arr = attr.array as Float32Array
    arr.fill(0)
    if (hoveredIndex !== null && hoveredIndex < arr.length) arr[hoveredIndex] = 0.6
    if (selectedIndex !== null && selectedIndex < arr.length) arr[selectedIndex] = 1
    attr.needsUpdate = true
  }, [hoveredIndex, selectedIndex, buffers])

  useFrame(() => {
    material.uniforms.uPixelRatio!.value = Math.min(gl.getPixelRatio(), 2)
  })

  if (!geometry) return null

  return (
    <points frustumCulled={false}>
      <primitive object={geometry} ref={geometryRef} attach="geometry" />
      <primitive object={material} attach="material" />
    </points>
  )
}
