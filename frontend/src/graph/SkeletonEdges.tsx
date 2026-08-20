/**
 * The faint global graph under the map.
 *
 * Scattered dots show where papers sit but not that they are related. Two
 * edges per paper — its nearest neighbours in embedding space — trace the
 * manifold the projection flattened, so clusters read as connected regions
 * rather than coincidental piles.
 *
 * Built once from the payload and never rebuilt: it is a property of the
 * projection, not of what the viewer is doing. One LineSegments, one draw
 * call, ~455 segments on the real corpus and ~6,000 at 4,000 papers.
 */
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'

import { useGraphStore } from '@/state/graphStore'

export function SkeletonEdges() {
  const skeleton = useGraphStore((s) => s.skeleton)
  const buffers = useGraphStore((s) => s.buffers)
  const previous = useRef<THREE.BufferGeometry | null>(null)

  useEffect(
    () => () => {
      previous.current?.dispose()
      previous.current = null
    },
    [],
  )

  const geometry = useMemo(() => {
    if (!skeleton || !buffers || skeleton.length === 0) return null

    const points = new Float32Array(skeleton.length * 3)
    for (let e = 0; e < skeleton.length; e++) {
      const node = skeleton[e]!
      points[e * 3] = buffers.positions[node * 3]!
      points[e * 3 + 1] = buffers.positions[node * 3 + 1]!
      points[e * 3 + 2] = buffers.positions[node * 3 + 2]!
    }

    previous.current?.dispose()
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(points, 3))
    previous.current = g
    return g
  }, [skeleton, buffers])

  if (!geometry) return null

  return (
    <lineSegments geometry={geometry} frustumCulled={false}>
      {/*
        Additive, and very faint. Individually an edge is almost invisible;
        where many overlap inside a cluster they accumulate into a glow, which
        is what makes dense regions read as dense rather than merely crowded.
      */}
      <lineBasicMaterial
        color="#3d6ea8"
        transparent
        opacity={0.14}
        blending={THREE.AdditiveBlending}
        depthWrite={false}
      />
    </lineSegments>
  )
}
