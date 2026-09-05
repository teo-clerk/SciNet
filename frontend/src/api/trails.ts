/**
 * The path from one idea to another, fetched.
 *
 * Each end is a paper id or a phrase. A phrase is anchored to its nearest
 * paper on the server, and the response says so — "from Ethics" is a claim
 * about the reader's words, "from paper 12" a claim about the corpus, and the
 * panel shows which one it got.
 */
import { EngineWarming } from '@/lib/search'
import { pathQuery, type TrailEnd, type TrailStop } from '@/lib/trail'

export interface PathEnd {
  paper_id: number
  /** The phrase this end was typed as, when it was anchored rather than given. */
  anchored_from_text: string | null
}

export interface PathResult {
  from: PathEnd
  to: PathEnd
  /** False when the kNN graph has no chain and the last hop is a leap. */
  complete: boolean
  hops: number
  /** True when a long chain was shortened to fit the panel. */
  thinned: boolean
  stops: TrailStop[]
}

export async function fetchPath(
  from: TrailEnd,
  to: TrailEnd,
  signal?: AbortSignal,
): Promise<PathResult> {
  const res = await fetch(`/api/graph/path?${pathQuery(from, to)}`, { signal })

  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    // The same three answers the search box tells apart: a model still
    // loading is a wait, a string is the server's own sentence, and anything
    // else is a status code the reader should at least see.
    if (detail && typeof detail === 'object' && detail.state === 'warming') {
      throw new EngineWarming(
        detail.elapsed_seconds ?? 0,
        detail.estimated_remaining ?? null,
      )
    }
    const message =
      typeof detail === 'string'
        ? detail
        : (detail?.message ?? `could not find a path (${res.status})`)
    throw new Error(message)
  }

  return (await res.json()) as PathResult
}
