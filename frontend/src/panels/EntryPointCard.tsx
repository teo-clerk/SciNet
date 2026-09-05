/**
 * Where to start, and why.
 *
 * Shown inside a region's inspector and in the floating card the filter bar
 * raises; the same component, because the answer has the same shape wherever
 * the question was asked. The reasons are the server's own phrases, shown as
 * chips and not rewritten — it ranked the papers, it gets to say why.
 */
import type { EntryPoint } from '@/api/clusters'

interface Props {
  entry: EntryPoint
  alternatives: EntryPoint[]
  /** Paper ids. The button is disabled when there is nothing to order. */
  readingOrder: number[]
  onOpen: (paperId: number) => void
  onReadingOrder: () => void
}

const nameOf = (point: EntryPoint) => point.title ?? `#${point.paper_id}`

export function EntryPointCard({ entry, alternatives, readingOrder, onOpen, onReadingOrder }: Props) {
  return (
    <section className="entry-point">
      <h3>Where to start</h3>
      <button className="entry-title" onClick={() => onOpen(entry.paper_id)}>
        {nameOf(entry)}
      </button>
      {entry.year !== null && <span className="dim year">{entry.year}</span>}

      {entry.reasons.length > 0 && (
        <div className="tag-list">
          {entry.reasons.map((reason) => (
            <span key={reason} className="chip">
              {reason}
            </span>
          ))}
        </div>
      )}

      {alternatives.length > 0 && (
        <p className="dim also-good">
          Also good:{' '}
          {alternatives.map((point, i) => (
            <span key={point.paper_id}>
              {i > 0 && ' · '}
              <button onClick={() => onOpen(point.paper_id)}>{nameOf(point)}</button>
            </span>
          ))}
        </p>
      )}

      <button
        className="reading-order"
        disabled={readingOrder.length < 2}
        onClick={onReadingOrder}
        title="Walk these works in the order they are best read"
      >
        Reading order
      </button>
    </section>
  )
}
