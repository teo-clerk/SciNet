/**
 * The trail on the map: a dashed line walking the stops, and their numbers.
 *
 * A Catmull-Rom curve through the nodes, drawn with drei's Line
 * (native WebGL lines ignore lineWidth — the BridgeCurves lesson) and
 * animated by mutating the material's dashOffset in useFrame, so the dashes
 * crawl along the path with no per-frame React work and no geometry
 * rebuild: the sampled points are memoized on the trail itself.
 *
 * The numbers are HTML, one per stop, so the panel's "3" and the map's "3"
 * are the same glyph in the same place. Capped: past fourteen the digits pile
 * up faster than they help, and the line alone still shows the route.
 */
import { Html, Line } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'

import { prefersReducedMotion } from '@/lib/motion'
import { MAX_MARKERS } from '@/lib/trail'
import { useGraphStore } from '@/state/graphStore'

const SEGMENTS_PER_HOP = 10
const DASH_SPEED = 6
const TRAIL_COLOR = '#ffc76e'
/** Below every panel (z 20+), above the canvas. */
const MARKER_Z_RANGE: [number, number] = [12, 0]

export function Trail() {
  const trail = useGraphStore((s) => s.trail)
  const cursor = useGraphStore((s) => s.trailCursor)
  const buffers = useGraphStore((s) => s.buffers)
  const lineRef = useRef<any>(null)
  // Asked once: the preference does not change mid-session often enough to
  // be worth a media-query listener in the render loop.
  const still = useRef(prefersReducedMotion())

  const anchors = useMemo(() => {
    if (!buffers || trail.length < 2) return null
    return trail.map(
      (i) =>
        new THREE.Vector3(
          buffers.positions[i * 3]!,
          buffers.positions[i * 3 + 1]!,
          buffers.positions[i * 3 + 2]!,
        ),
    )
  }, [trail, buffers])

  const points = useMemo(
    () =>
      anchors
        ? new THREE.CatmullRomCurve3(anchors).getPoints(anchors.length * SEGMENTS_PER_HOP)
        : null,
    [anchors],
  )

  useFrame((_, delta) => {
    if (still.current) return
    const material = lineRef.current?.material
    if (material) {
      material.dashOffset -= delta * DASH_SPEED
    }
  })

  if (!points || !anchors) return null
  return (
    <>
      <Line
        ref={lineRef}
        points={points}
        color={TRAIL_COLOR}
        lineWidth={2.2}
        dashed
        dashSize={1.6}
        gapSize={1.1}
        transparent
        opacity={0.85}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
      {anchors.length <= MAX_MARKERS &&
        anchors.map((position, i) => (
          <Html
            key={`${trail[i]}-${i}`}
            position={position}
            center
            zIndexRange={MARKER_Z_RANGE}
            pointerEvents="none"
            className={i === cursor ? 'trail-marker current' : 'trail-marker'}
          >
            {i + 1}
          </Html>
        ))}
    </>
  )
}
