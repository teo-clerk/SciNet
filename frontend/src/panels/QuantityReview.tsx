/**
 * The quantity review queue: the LLM's doubt, resolved by a person.
 *
 * Each row shows the sentence (the whole provenance), what was written,
 * and what the adjudicator thought — confirm moves it into the filterable
 * set immediately, reject removes it for good. The quarantine view's
 * pattern: a plain table, verbs on the row, nothing clever.
 */
import { useEffect, useState } from 'react'

import {
  fetchReviewQueue,
  reviewQuantity,
  type ReviewRow,
} from '@/api/quantities'
import { formatQuantity } from '@/lib/quantities'

export function QuantityReview() {
  const [rows, setRows] = useState<ReviewRow[]>([])
  const [loaded, setLoaded] = useState(false)

  const reload = () => {
    void fetchReviewQueue().then((queue) => {
      setRows(queue)
      setLoaded(true)
    })
  }
  useEffect(reload, [])

  const decide = (id: number, verdict: 'confirm' | 'reject') => {
    void reviewQuantity(id, verdict).then(reload)
  }

  if (!loaded) return <div className="quantity-review placeholder">Loading…</div>
  if (rows.length === 0) {
    return (
      <div className="quantity-review placeholder">
        <p className="dim">Nothing awaits review — the queue is clear.</p>
      </div>
    )
  }

  return (
    <div className="quantity-review">
      <table>
        <thead>
          <tr>
            <th>paper</th>
            <th>as written</th>
            <th>adjudicated as</th>
            <th>sentence</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="dim">{row.paper_title ?? `#${row.paper_id}`}</td>
              <td>
                “{row.value_original} {row.unit_original}”
              </td>
              <td>
                {row.quantity_kind === 'unknown'
                  ? '—'
                  : `${row.quantity_kind}: ${formatQuantity(row.value_si, row.unit_si, row.quantity_kind)}`}
              </td>
              <td className="sentence">{row.context_sentence}</td>
              <td className="verbs">
                <button className="ghost" onClick={() => decide(row.id, 'confirm')}>
                  Confirm
                </button>
                <button
                  className="ghost warn"
                  onClick={() => decide(row.id, 'reject')}
                >
                  Reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
