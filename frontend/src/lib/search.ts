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

export async function searchServer(
  mode: 'fulltext' | 'semantic',
  query: string,
  signal?: AbortSignal,
): Promise<ServerHit[]> {
  const res = await fetch(
    `/api/search/${mode}?q=${encodeURIComponent(query)}&limit=60`,
    { signal },
  )
  if (!res.ok) {
    // 503 means the embedding model is not loaded; that is worth saying rather
    // than showing an empty result the user will read as "nothing matched".
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `search failed (${res.status})`)
  }
  const body = (await res.json()) as { hits: ServerHit[] }
  return body.hits
}
