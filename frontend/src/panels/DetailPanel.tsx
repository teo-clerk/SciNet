/**
 * The selected paper.
 *
 * Detail is fetched per node on click. Shipping abstracts for the whole corpus
 * up front is what turns a 50 ms map load into a multi-second one.
 */
import { useEffect, useState } from 'react'

import { fetchNeighbours, fetchPaper, type Neighbour, type PaperDetail } from '@/api/graph'
import { useGraphStore } from '@/state/graphStore'

export function DetailPanel() {
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const nodes = useGraphStore((s) => s.nodes)
  const setSelected = useGraphStore((s) => s.setSelected)

  const [paper, setPaper] = useState<PaperDetail | null>(null)
  const [neighbours, setNeighbours] = useState<Neighbour[]>([])
  const [opening, setOpening] = useState(false)

  const node = selectedIndex === null ? null : nodes[selectedIndex]

  useEffect(() => {
    if (!node) {
      setPaper(null)
      setNeighbours([])
      return
    }
    let cancelled = false
    setPaper(null)
    void fetchPaper(node.id).then((p) => !cancelled && setPaper(p)).catch(() => {})
    void fetchNeighbours(node.id, 5).then((n) => !cancelled && setNeighbours(n)).catch(() => {})
    return () => {
      cancelled = true
    }
  }, [node])

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
        {node.provisional && (
          <span className="chip warn" title="Placed without a full refit">
            provisional · drift {node.drift}
          </span>
        )}
        {paper?.parse && <span className="chip dim">tier {paper.parse.tier}</span>}
        {paper?.page_count && <span className="chip dim">{paper.page_count} pp</span>}
      </div>

      {paper?.authors?.length ? (
        <p className="authors">{paper.authors.slice(0, 8).join(', ')}</p>
      ) : null}

      {paper?.summary && (
        <section>
          <h3>Summary</h3>
          <p>{paper.summary}</p>
        </section>
      )}

      {paper?.abstract && (
        <section>
          <h3>Abstract</h3>
          <p className="abstract">{paper.abstract}</p>
        </section>
      )}

      {neighbours.length > 0 && (
        <section>
          <h3>Nearest in embedding space</h3>
          <ul className="neighbours">
            {neighbours.map((hit) => (
              <li key={hit.id}>
                <button onClick={() => focusNeighbour(hit.id)}>
                  <span className="score">{hit.similarity.toFixed(3)}</span>
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
