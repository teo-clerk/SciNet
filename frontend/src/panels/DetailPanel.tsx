/**
 * The selected paper.
 *
 * Detail is fetched per node on click. Shipping abstracts for the whole corpus
 * up front is what turns a 50 ms map load into a multi-second one.
 *
 * Top to bottom the panel goes plain to technical: the idea in plain words,
 * then the abstract (folded once there is something plainer above it), the
 * two-sentence summary, the measured values and the claims they support, and
 * only then where the map put it. The sections live in ./detail; this file is
 * the fetching and the order.
 */
import { useEffect, useRef, useState } from 'react'

import { fetchNeighbours, fetchPaper, type Neighbour, type PaperDetail } from '@/api/graph'
import { fetchPaperQuantities, type QuantityRow } from '@/api/quantities'
import { subscribeToEvents } from '@/api/sse'
import { pagesLabel } from '@/lib/plain'
import { CoreIdea } from '@/panels/detail/CoreIdea'
import { KeyIdeas } from '@/panels/detail/KeyIdeas'
import { MeasuredValues } from '@/panels/detail/MeasuredValues'
import { Placement } from '@/panels/detail/Placement'
import { useGraphStore } from '@/state/graphStore'

export function DetailPanel() {
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const nodes = useGraphStore((s) => s.nodes)
  const setSelected = useGraphStore((s) => s.setSelected)
  const labMode = useGraphStore((s) => s.labMode)

  const [paper, setPaper] = useState<PaperDetail | null>(null)
  const [neighbours, setNeighbours] = useState<Neighbour[]>([])
  const [quantities, setQuantities] = useState<QuantityRow[]>([])
  const [opening, setOpening] = useState(false)

  const node = selectedIndex === null ? null : nodes[selectedIndex]
  const paperId = node?.id ?? null
  // What the event stream compares against; a ref, so the one subscription
  // below outlives every selection instead of reconnecting per click.
  const shownId = useRef<number | null>(null)

  useEffect(() => {
    shownId.current = paperId
    if (paperId === null) {
      setPaper(null)
      setNeighbours([])
      setQuantities([])
      return
    }
    let cancelled = false
    setPaper(null)
    void fetchPaper(paperId).then((p) => !cancelled && setPaper(p)).catch(() => {})
    void fetchPaperQuantities(paperId)
      .then((rows) => !cancelled && setQuantities(rows))
      .catch(() => {})
    void fetchNeighbours(paperId, 5).then((n) => !cancelled && setNeighbours(n)).catch(() => {})
    return () => {
      cancelled = true
    }
  }, [paperId])

  // The plain-English reading is written after tagging, often while the
  // panel is open on a freshly added work. Re-fetch when the worker says it
  // has landed, so the section fills in without the reader clicking away.
  useEffect(
    () =>
      subscribeToEvents((event) => {
        const id = shownId.current
        if (id === null || event.kind !== 'insight.done' || event.detail.paper_id !== id) return
        void fetchPaper(id)
          .then((p) => shownId.current === id && setPaper(p))
          .catch(() => {})
      }),
    [],
  )

  if (!node) return null

  const openInViewer = async () => {
    setOpening(true)
    try {
      const res = await fetch(`/api/papers/${node.id}/open`, { method: 'POST' })
      if (!res.ok) alert(`Could not open the PDF (${res.status})`)
    } finally {
      setOpening(false)
    }
  }

  const focusNeighbour = (id: number) => {
    const index = nodes.findIndex((n) => n.id === id)
    if (index >= 0) setSelected(index)
  }

  return (
    <aside className="detail-panel">
      <button className="close" onClick={() => setSelected(null)} aria-label="Close">
        ×
      </button>

      <h2>{paper?.title ?? node.title ?? '(untitled)'}</h2>

      <div className="meta-row">
        {paper?.year && <span className="chip">{paper.year}</span>}
        {labMode && node.provisional && (
          <span className="chip warn" title="Placed without a full refit">
            provisional · drift {node.drift}
          </span>
        )}
        {labMode && paper?.parse && <span className="chip dim">tier {paper.parse.tier}</span>}
        {paper && pagesLabel(paper.page_count) && (
          <span className="chip dim">{pagesLabel(paper.page_count)}</span>
        )}
      </div>

      {paper?.authors?.length ? (
        <p className="authors">{paper.authors.slice(0, 8).join(', ')}</p>
      ) : null}

      {paper?.tags?.length ? (
        <div className="tag-list">
          {paper.tags.map((tag) => (
            <span key={tag} className="chip tag-chip">
              {tag}
            </span>
          ))}
        </div>
      ) : null}

      {paper && <CoreIdea paper={paper} />}

      {paper?.abstract && (
        /* Folded once the plain reading exists above it: the abstract is
           still there for whoever wants the author's own words, but it no
           longer stands between the reader and the idea. */
        <details className="academic" open={!paper.insight}>
          <summary>Academic abstract</summary>
          {paper.abstract_source === 'extracted_digest' && (
            /* A book has no abstract, so this one was assembled from the
               book's own paragraphs. Saying so matters: presented plainly it
               reads as the author's summary of their work, and it is not. */
            <p className="assembled">Assembled from the text — this document has no abstract of its own.</p>
          )}
          <p className="abstract prose">{paper.abstract}</p>
        </details>
      )}

      {paper?.summary && (
        <section>
          <h3>In two sentences</h3>
          <p className="prose">{paper.summary}</p>
        </section>
      )}

      <MeasuredValues quantities={quantities} labMode={labMode} />

      {paper?.insight && <KeyIdeas insight={paper.insight} />}

      {paper && <Placement paper={paper} labMode={labMode} />}

      {neighbours.length > 0 && (
        <section>
          <h3>Closest in meaning</h3>
          <ul className="neighbours">
            {neighbours.map((hit) => (
              <li key={hit.id}>
                <button onClick={() => focusNeighbour(hit.id)}>
                  {labMode && <span className="score">{hit.similarity.toFixed(3)}</span>}
                  {hit.title ?? `#${hit.id}`}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="actions">
        <button onClick={openInViewer} disabled={opening}>
          {opening ? 'Opening…' : 'Open PDF'}
        </button>
        {paper?.doi && (
          <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">
            DOI
          </a>
        )}
        {paper?.arxiv_id && (
          <a
            href={`https://arxiv.org/abs/${paper.arxiv_id}`}
            target="_blank"
            rel="noreferrer"
          >
            arXiv
          </a>
        )}
      </div>
    </aside>
  )
}
