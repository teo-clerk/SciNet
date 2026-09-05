/**
 * A region of the map, opened.
 *
 * Replaces the floating per-node titles that used to crowd the 3D view: at any
 * useful zoom they overlapped into unreadable text and hid the structure they
 * were meant to annotate. The cluster labels stay on the map because there are
 * only a handful; everything about a region's contents lives here instead,
 * fetched when asked for rather than shipped with the graph.
 *
 * Top to bottom: what the region is, where to start reading it, the words
 * that recur, the regions it reaches toward, and the works themselves — the
 * list last, because it takes the leftover height and scrolls on its own.
 */
import { useEffect, useState } from 'react'

import { fetchCluster, type ClusterDetail } from '@/api/clusters'
import { useReadingOrder } from '@/lib/useReadingOrder'
import { BridgesFromHere } from '@/panels/BridgesFromHere'
import { EntryPointCard } from '@/panels/EntryPointCard'
import { useGraphStore } from '@/state/graphStore'

export function ClusterInspector() {
  const clusterId = useGraphStore((s) => s.inspectedCluster)
  const inspect = useGraphStore((s) => s.inspectCluster)
  const nodes = useGraphStore((s) => s.nodes)
  const setSelected = useGraphStore((s) => s.setSelected)
  const setView = useGraphStore((s) => s.setView)
  const labMode = useGraphStore((s) => s.labMode)
  const startReadingOrder = useReadingOrder()

  const [detail, setDetail] = useState<ClusterDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (clusterId === null) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetail(null)
    setError(null)
    fetchCluster(clusterId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e: unknown) =>
        !cancelled && setError(e instanceof Error ? e.message : String(e)),
      )
    return () => {
      cancelled = true
    }
  }, [clusterId])

  if (clusterId === null) return null

  const focusPaper = (paperId: number) => {
    const index = nodes.findIndex((n) => n.id === paperId)
    if (index < 0) return
    // Back to the map first, so the camera flight is something the reader sees
    // rather than something that already happened while they were elsewhere.
    setView('map')
    setSelected(index)
  }

  return (
    <aside className="cluster-inspector">
      <button className="close" onClick={() => inspect(null)} aria-label="Close">
        ×
      </button>

      {error && <p className="warn">{error}</p>}
      {!detail && !error && <p className="dim">Loading region…</p>}

      {detail && (
        <>
          <h2>{detail.label ?? 'Unnamed region'}</h2>
          <div className="meta-row">
            <span className="chip">{detail.size} papers</span>
          </div>

          {detail.overview && (
            <section>
              <h3>What this region is</h3>
              <p className="prose">{detail.overview}</p>
            </section>
          )}

          {detail.entry_point && (
            <EntryPointCard
              entry={detail.entry_point}
              alternatives={detail.alternatives}
              readingOrder={detail.reading_order}
              onOpen={focusPaper}
              onReadingOrder={() => startReadingOrder(detail.reading_order)}
            />
          )}

          {detail.terms.length > 0 && (
            <section>
              <h3>Recurring words</h3>
              <div className="tag-list">
                {detail.terms.slice(0, 14).map((term) => (
                  <span key={term} className="chip">
                    {term}
                  </span>
                ))}
              </div>
            </section>
          )}

          <BridgesFromHere clusterId={clusterId} />

          <section>
            <h3>Works ({detail.members.length})</h3>
            <ul className="member-list">
              {detail.members.map((member) => (
                <li key={member.paper_id}>
                  <button onClick={() => focusPaper(member.paper_id)}>
                    <span className="member-title">
                      {member.title ?? `#${member.paper_id}`}
                    </span>
                    <span className="member-meta">
                      {member.first_author && <span>{member.first_author}</span>}
                      {member.year && <span>{member.year}</span>}
                      {labMode && member.confidence !== null && (
                        <span
                          className={member.confidence < 0.5 ? 'warn' : 'dim'}
                          title="How firmly this paper belongs to the region"
                        >
                          {member.confidence.toFixed(2)}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </aside>
  )
}
