/**
 * The quantities API: kind summaries for the filter, paper ids for the
 * visible set, per-paper rows for the detail card, and the review queue.
 */

export interface KindSummary {
  kind: string
  unit_si: string | null
  count: number
  min_value: number
  max_value: number
}

export interface QuantityRow {
  id: number
  paper_id: number
  quantity_kind: string
  value_si: number | null
  unit_si: string | null
  value_original: string
  unit_original: string
  context_sentence: string
  section: string | null
  confidence: number
  extraction_source: string
  status: string
}

export interface ReviewRow extends QuantityRow {
  paper_title: string | null
}

export async function fetchKinds(): Promise<KindSummary[]> {
  const res = await fetch('/api/quantities/kinds')
  if (!res.ok) return []
  return (await res.json()) as KindSummary[]
}

export async function searchQuantities(
  kind: string,
  min: number | null,
  max: number | null,
): Promise<Set<number>> {
  const params = new URLSearchParams({ kind })
  if (min !== null) params.set('min_value', String(min))
  if (max !== null) params.set('max_value', String(max))
  const res = await fetch(`/api/quantities/search?${params}`)
  if (!res.ok) return new Set()
  const body = (await res.json()) as { paper_ids: number[] }
  return new Set(body.paper_ids)
}

export async function fetchPaperQuantities(
  paperId: number,
): Promise<QuantityRow[]> {
  const res = await fetch(`/api/quantities/paper/${paperId}`)
  if (!res.ok) return []
  return (await res.json()) as QuantityRow[]
}

export async function fetchReviewQueue(): Promise<ReviewRow[]> {
  const res = await fetch('/api/quantities/review')
  if (!res.ok) return []
  return (await res.json()) as ReviewRow[]
}

export async function reviewQuantity(
  id: number,
  verdict: 'confirm' | 'reject',
): Promise<void> {
  await fetch(`/api/quantities/${id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ verdict }),
  })
}
