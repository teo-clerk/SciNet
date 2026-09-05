/**
 * The regions this one reaches toward.
 *
 * The same links the map draws as curves, listed from one region's point of
 * view so the reader can follow one without hunting for its far end in 3D.
 * Fetched once per opening of the inspector: the link table is small and
 * changes only when the map is refitted.
 */
import { useEffect, useMemo, useState } from 'react'

import { fetchClusterLinks, type ClusterLink } from '@/api/clusters'
import { bridgesFrom } from '@/lib/bridges'
import { useGraphStore } from '@/state/graphStore'

export function BridgesFromHere({ clusterId }: { clusterId: number }) {
  const inspect = useGraphStore((s) => s.inspectCluster)
  const [links, setLinks] = useState<ClusterLink[]>([])

  useEffect(() => {
    let alive = true
    void fetchClusterLinks().then((all) => alive && setLinks(all))
    return () => {
      alive = false
    }
  }, [])

  const bridges = useMemo(() => bridgesFrom(links, clusterId), [links, clusterId])
  if (bridges.length === 0) return null

  return (
    <section>
      <h3>Bridges from here</h3>
      <ul className="bridges">
        {bridges.map((bridge) => (
          <li key={bridge.otherId}>
            <button onClick={() => inspect(bridge.otherId)}>
              ↔ {bridge.otherLabel ?? 'Unnamed region'}
            </button>
            {bridge.summary && <p className="prose">{bridge.summary}</p>}
          </li>
        ))}
      </ul>
    </section>
  )
}
