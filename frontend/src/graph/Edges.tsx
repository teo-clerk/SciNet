/**
 * Semantic neighbours of the selected node.
 *
 * Drawn only on selection. A global k-nearest graph would be ~40,000 segments
 * at 4,000 nodes and would read as a grey haze that hides the clusters it was
 * meant to explain.
 *
 * The neighbours come from the server, computed in the 1024-D embedding space.
 * They are deliberately *not* the nearest nodes on screen: UMAP distorts global
 * distance, so the two answers differ, and only the embedding one is true.
 */
import { useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'

import { fetchNeighbours, type Neighbour } from '@/api/graph'
import { useGraphStore } from '@/state/graphStore'

export function Edges() {
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const nodes = useGraphStore((s) => s.nodes)
  const buffers = useGraphStore((s) => s.buffers)
  const [neighbours, setNeighbours] = useState<Neighbour[]>([])

  useEffect(() => {
    if (selectedIndex === null) {
      setNeighbours([])
      return
    }
    const node = nodes[selectedIndex]
    if (!node) return

    let cancelled = false
    fetchNeighbours(node.id, 5)
      .then((hits) => {
        if (!cancelled) setNeighbours(hits)
      })
      .catch(() => setNeighbours([]))
    return () => {
      cancelled = true
    }
  }, [selectedIndex, nodes])

  const geometry = useMemo(() => {
    if (selectedIndex === null || !buffers || neighbours.length === 0) return null

    const indexById = new Map(nodes.map((n, i) => [n.id, i]))
    const points: number[] = []
    const origin = [
      buffers.positions[selectedIndex * 3]!,
      buffers.positions[selectedIndex * 3 + 1]!,
      buffers.positions[selectedIndex * 3 + 2]!,
    ]

    for (const hit of neighbours) {
      const target = indexById.get(hit.id)
      if (target === undefined) continue
      points.push(
        origin[0]!, origin[1]!, origin[2]!,
        buffers.positions[target * 3]!,
        buffers.positions[target * 3 + 1]!,
        buffers.positions[target * 3 + 2]!,
      )
    }
    if (points.length === 0) return null

    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(points, 3))
    return g
  }, [selectedIndex, neighbours, buffers, nodes])

  if (!geometry) return null

  return (
    <lineSegments geometry={geometry}>
      <lineBasicMaterial
        color="#7fd4ff"
        transparent
        opacity={0.55}
        depthWrite={false}
      />
    </lineSegments>
  )
}
