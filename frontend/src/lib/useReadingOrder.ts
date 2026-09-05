/**
 * Turn a reading order into a trail on the map.
 *
 * Two places offer one — a region's inspector and the floating "where to
 * start" card — and both want the same thing to happen: the stops built from
 * what the map already knows, the trail drawn and lit, the camera framing
 * the whole of it, and the panel open in reading-order mode so the reader can
 * step through. The paper-id to node-index crossing happens once, in lib/trail.
 */
import { useCallback } from 'react'

import { frameForSet } from '@/lib/framing'
import { stopsFromNodes, stopsToIndices } from '@/lib/trail'
import { useGraphStore } from '@/state/graphStore'

export function useReadingOrder(): (paperIds: number[]) => void {
  const nodes = useGraphStore((s) => s.nodes)
  const clusters = useGraphStore((s) => s.clusters)
  const buffers = useGraphStore((s) => s.buffers)
  const setView = useGraphStore((s) => s.setView)
  const setTrailResult = useGraphStore((s) => s.setTrailResult)
  const flyToPoint = useGraphStore((s) => s.flyToPoint)

  return useCallback(
    (paperIds: number[]) => {
      const stops = stopsFromNodes(paperIds, nodes, clusters)
      if (stops.length < 2) return
      // Back to the map first: the flight should be something the reader
      // watches, not something that already happened behind the list view.
      setView('map')
      setTrailResult(stops, true, 'reading-order')
      if (!buffers) return
      const framing = frameForSet(stopsToIndices(stops, nodes), buffers.positions)
      if (framing) flyToPoint(framing.target, framing.distance)
    },
    [nodes, clusters, buffers, setView, setTrailResult, flyToPoint],
  )
}
