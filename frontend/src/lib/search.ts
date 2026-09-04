/**
 * Three search modes, kept distinct.
 *
 * *Title* runs in the browser over the payload already in memory: no round
 * trip, results as fast as the user types. It only knows titles.
 *
 * *Full text* asks the server's FTS5 index over the parsed Markdown. Exact,
 * and finds phrases inside a paper's body that its title never mentions.
 *
 * *Semantic* asks the server to embed the query and compare it to the document
 * vectors. Finds papers that mean something similar even when they share no
 * vocabulary with the query — and will always return its k nearest, however
 * unrelated, which is why it is never blended into the others.
 */
import MiniSearch from 'minisearch'

import type { GraphNode } from '@/api/graph'

export type SearchMode = 'title' | 'fulltext' | 'semantic'

export interface ServerHit {
  paper_id: number
  title: string | null
  score: number
  snippet?: string | null
  section?: string | null
}

let index: MiniSearch<GraphNode> | null = null
let indexedRun: number | null = null

/** Built once per projection run; rebuilding on every keystroke would stutter. */
export function buildTitleIndex(nodes: GraphNode[], runId: number): void {
  if (indexedRun === runId && index) return
  const next = new MiniSearch<GraphNode>({
    fields: ['title'],
    storeFields: ['id'],
    idField: 'id',
    searchOptions: {
      prefix: true,
      // Typo tolerance scaled to term length: strict on short words, where a
      // single edit turns one real word into another.
      fuzzy: (term) => (term.length > 5 ? 0.2 : term.length > 3 ? 0.1 : 0),
      boost: { title: 2 },
    },
  })
  next.addAll(nodes.filter((n) => n.title))
  index = next
  indexedRun = runId
}

/** Paper ids matching a title query, best first. */
export function searchTitles(query: string): number[] {
  if (!index || !query.trim()) return []
  return index.search(query).map((r) => r.id as number)
}

/**
 * The search engine is still loading its model.
 *
 * Distinct from a failure: the right response is to wait and retry, not to
 * tell the reader the feature is broken.
 */
export class EngineWarming extends Error {
  constructor(
    readonly elapsedSeconds: number,
    readonly estimatedRemaining: number | null,
  ) {
    super('the search engine is still starting up')
    this.name = 'EngineWarming'
  }
}

/** Exact matches are what they are; the cap only bounds the payload. */
export const FULLTEXT_LIMIT = 60

/**
 * How many nearest papers a meaning search lights up.
 *
 * Semantic search always returns its k nearest, however unrelated, so k is
 * the whole answer to "how much of the map lights up". A fixed 60 was right
 * for a five-hundred-paper library and lit 70% of an 85-paper one — the
 * result read as "nearly everything matched", which said nothing. The same
 * square-root law the projection uses: a library ten times larger does not
 * have ten times as many papers about one thing.
 */
export function semanticLimit(paperCount: number): number {
  const scaled = Math.round(2 * Math.sqrt(Math.max(paperCount, 0)))
  return Math.min(FULLTEXT_LIMIT, Math.max(12, scaled))
}

export async function searchServer(
  mode: 'fulltext' | 'semantic',
  query: string,
  signal?: AbortSignal,
  limit: number = FULLTEXT_LIMIT,
): Promise<ServerHit[]> {
  const res = await fetch(
    `/api/search/${mode}?q=${encodeURIComponent(query)}&limit=${limit}`,
    { signal },
  )

  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    // An empty result would read as "nothing matched", which is a different
    // and false statement, so every failure mode says what it actually is.
    if (detail && typeof detail === 'object' && detail.state === 'warming') {
      throw new EngineWarming(
        detail.elapsed_seconds ?? 0,
        detail.estimated_remaining ?? null,
      )
    }
    const message =
      typeof detail === 'string'
        ? detail
        : (detail?.message ?? `search failed (${res.status})`)
    throw new Error(message)
  }

  const body = (await res.json()) as { hits: ServerHit[] }
  return body.hits
}
