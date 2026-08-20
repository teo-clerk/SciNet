/**
 * The node renderer: one THREE.Points, one draw call.
 *
 * Everything per-node is a BufferAttribute. Hover/select/filter update the
 * attribute arrays and flag `needsUpdate` — no geometry rebuilds, no React
 * re-renders, no allocation on the hot path.
 */
import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'

import fragmentShader from './shaders/point.frag.glsl?raw'
import vertexShader from './shaders/point.vert.glsl?raw'
import { useGraphStore } from '@/state/graphStore'

const FOG_COLOR = new THREE.Color('#070912')

export function PointCloud() {
  const buffers = useGraphStore((s) => s.buffers)
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const hoveredIndex = useGraphStore((s) => s.hoveredIndex)

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
    g.computeBoundingSphere()
    return g
  }, [buffers])

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
          uPixelRatio: { value: Math.min(gl.getPixelRatio(), 2) },
          uBaseSize: { value: 3.2 },
          uFogColor: { value: FOG_COLOR },
          uFogNear: { value: 70.0 },
          uFogFar: { value: 260.0 },
        },
        transparent: true,
        // depthWrite is OFF deliberately. Soft sprites that write depth occlude
        // each other at the rim, which reads as a dark halo around every node
        // (very visible once clusters overlap). Blending without depth writes
        // costs correct front-to-back ordering between points, which at this
        // dot size is imperceptible, and buys clean overlap.
        depthWrite: false,
        depthTest: true,
      }),
    [gl],
  )

  // Highlight state is written straight into the attribute array.
  useEffect(() => {
    const g = geometryRef.current
    if (!g || !buffers) return
    const attr = g.getAttribute('aSelected') as THREE.BufferAttribute
    const arr = attr.array as Float32Array
    arr.fill(0)
    if (selectedIndex !== null && selectedIndex < arr.length) arr[selectedIndex] = 1
    if (hoveredIndex !== null && hoveredIndex < arr.length) {
      arr[hoveredIndex] = Math.max(arr[hoveredIndex]!, 0.6)
    }
    attr.needsUpdate = true
  }, [selectedIndex, hoveredIndex, buffers])

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
