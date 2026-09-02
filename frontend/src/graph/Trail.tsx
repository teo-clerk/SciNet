/**
 * The trail of the evidence: a dashed line walking the librarian's citations.
 *
 * A Catmull-Rom curve through the cited nodes, drawn with drei's Line
 * (native WebGL lines ignore lineWidth — the BridgeCurves lesson) and
 * animated by mutating the material's dashOffset in useFrame, so the dashes
 * crawl along the path with no per-frame React work and no geometry
 * rebuild: the sampled points are memoized on the trail itself.
 */
import { Line } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useMemo, useRef } from 'react'
import * as THREE from 'three'

import { useGraphStore } from '@/state/graphStore'

const SEGMENTS_PER_HOP = 10
const DASH_SPEED = 6
const TRAIL_COLOR = '#ffc76e'

export function Trail() {
  const trail = useGraphStore((s) => s.trail)
  const buffers = useGraphStore((s) => s.buffers)
  const lineRef = useRef<any>(null)

  const points = useMemo(() => {
    if (!buffers || trail.length < 2) return null
    const anchors = trail.map(
      (i) =>
        new THREE.Vector3(
          buffers.positions[i * 3]!,
          buffers.positions[i * 3 + 1]!,
          buffers.positions[i * 3 + 2]!,
        ),
    )
    return new THREE.CatmullRomCurve3(anchors).getPoints(
      anchors.length * SEGMENTS_PER_HOP,
    )
  }, [trail, buffers])

  useFrame((_, delta) => {
    const material = lineRef.current?.material
    if (material) {
      material.dashOffset -= delta * DASH_SPEED
    }
  })

  if (!points) return null
  return (
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
  )
}
