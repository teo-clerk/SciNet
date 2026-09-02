/**
 * Filter the map by a measured range: "altitude 400–800 km" as geometry.
 *
 * Renders only when the library actually holds trusted quantities. The
 * matched paper ids ride the same visible-set machinery as search and tags
 * — the renderer needs no changes at all. Inputs are in the kind's
 * canonical unit (shown beside them): honest, if unglamorous, and the
 * bounds placeholder tells the reader the corpus's actual range.
 */
import { useEffect, useRef, useState } from 'react'

import { fetchKinds, searchQuantities, type KindSummary } from '@/api/quantities'
import { formatQuantity } from '@/lib/quantities'
import { useGraphStore } from '@/state/graphStore'

const DEBOUNCE_MS = 400

export function QuantityFilter() {
  const runId = useGraphStore((s) => s.runId)
  const setQuantityResults = useGraphStore((s) => s.setQuantityResults)
  const quantityResults = useGraphStore((s) => s.quantityResults)

  const [kinds, setKinds] = useState<KindSummary[]>([])
  const [kind, setKind] = useState('')
  const [minText, setMinText] = useState('')
  const [maxText, setMaxText] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let live = true
    void fetchKinds().then((all) => {
      if (live) setKinds(all)
    })
    return () => {
      live = false
    }
  }, [runId])

  // The store clears the filter (clearFilters, a new graph); the inputs
  // must follow or they claim a filter that is not applied.
  useEffect(() => {
    if (quantityResults === null && kind) {
      setKind('')
      setMinText('')
      setMaxText('')
    }
  }, [quantityResults]) // eslint-disable-line react-hooks/exhaustive-deps

  if (kinds.length === 0) return null
  const summary = kinds.find((k) => k.kind === kind) ?? null

  const apply = (nextKind: string, minRaw: string, maxRaw: string) => {
    if (timer.current) clearTimeout(timer.current)
    if (!nextKind) {
      setQuantityResults(null)
      return
    }
    timer.current = setTimeout(() => {
      const parse = (raw: string) => (raw.trim() === '' ? null : Number(raw))
      void searchQuantities(nextKind, parse(minRaw), parse(maxRaw)).then(
        setQuantityResults,
      )
    }, DEBOUNCE_MS)
  }

  return (
    <div className="quantity-filter" title="Filter papers by a measured value">
      <select
        value={kind}
        onChange={(event) => {
          const next = event.target.value
          setKind(next)
          apply(next, minText, maxText)
        }}
      >
        <option value="">measured…</option>
        {kinds.map((k) => (
          <option key={k.kind} value={k.kind}>
            {k.kind} ({k.count})
          </option>
        ))}
      </select>
      {summary && (
        <>
          <input
            type="number"
            value={minText}
            placeholder={String(summary.min_value)}
            aria-label="Minimum value"
            onChange={(event) => {
              setMinText(event.target.value)
              apply(kind, event.target.value, maxText)
            }}
          />
          <span className="dim">–</span>
          <input
            type="number"
            value={maxText}
            placeholder={String(summary.max_value)}
            aria-label="Maximum value"
            onChange={(event) => {
              setMaxText(event.target.value)
              apply(kind, minText, event.target.value)
            }}
          />
          <span className="dim unit">
            {summary.unit_si ?? ''} · {formatQuantity(summary.min_value, summary.unit_si, kind)}
            {' to '}
            {formatQuantity(summary.max_value, summary.unit_si, kind)}
          </span>
        </>
      )}
    </div>
  )
}
