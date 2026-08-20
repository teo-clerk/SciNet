/**
 * Graph payload decoding.
 *
 * Positions arrive as a base64 Float32Array rather than JSON numbers: 48 KB for
 * 4,000 nodes against roughly 20 MB of equivalent JSON, and the decoded buffer
 * is handed straight to a BufferAttribute with no per-node parsing.
 */

export interface GraphNode {
  id: number
  title: string | null
  year: number | null
  cluster: number | null
  tags: number[]
  /** Placed by transform() against a stored fit rather than by a full refit. */
  provisional: boolean
  /** How far the paper sits from the fitted manifold; ~1 is typical. */
  drift: number
}

export interface GraphCluster {
  id: number
  label: string | null
  size: number
  terms: string[]
}

export interface GraphPayload {
  run_id: number
  method: string
  count: number
  positions_f32: string
  tag_vocabulary: string[]
  clusters: GraphCluster[]
  nodes: GraphNode[]
}

export interface DecodedGraph {
  runId: number
  method: string
  positions: Float32Array
  nodes: GraphNode[]
  clusters: GraphCluster[]
  tagVocabulary: string[]
}

function base64ToFloat32(encoded: string): Float32Array {
  const binary = atob(encoded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  // The buffer is already 4-byte aligned because it came from a fresh
  // allocation; a view avoids copying it a second time.
  return new Float32Array(bytes.buffer, 0, bytes.length / 4)
}

export function decodeGraph(payload: GraphPayload): DecodedGraph {
  const positions = base64ToFloat32(payload.positions_f32)
  if (positions.length !== payload.count * 3) {
    throw new Error(
      `graph payload is inconsistent: ${positions.length / 3} positions for ` +
        `${payload.count} nodes`,
    )
  }
  return {
    runId: payload.run_id,
    method: payload.method,
    positions,
    nodes: payload.nodes,
    clusters: payload.clusters,
    tagVocabulary: payload.tag_vocabulary,
  }
}

/** Cached across reloads by the ETag the server sets on the projection run. */
export async function fetchGraph(): Promise<DecodedGraph> {
  const res = await fetch('/api/graph', { headers: { Accept: 'application/json' } })
  if (res.status === 409) {
    throw new Error('No projection has been computed yet — run the worker.')
  }
  if (!res.ok) throw new Error(`/api/graph -> ${res.status} ${res.statusText}`)
  return decodeGraph((await res.json()) as GraphPayload)
}

export interface PaperDetail {
  id: number
  title: string | null
  authors: string[]
  abstract: string | null
  summary: string | null
  year: number | null
  venue: string | null
  doi: string | null
  arxiv_id: string | null
  status: string
  page_count: number | null
  parse: { tier: number; parser: string; quality_score: number | null } | null
}

/** Fetched per node on click — never for the whole corpus. */
export async function fetchPaper(id: number): Promise<PaperDetail> {
  const res = await fetch(`/api/papers/${id}`)
  if (!res.ok) throw new Error(`paper ${id} -> ${res.status}`)
  return (await res.json()) as PaperDetail
}

export interface Neighbour {
  id: number
  title: string | null
  similarity: number
}

/**
 * Nearest neighbours in the 1024-D embedding space.
 *
 * Deliberately a server round-trip rather than a distance computed on the
 * rendered coordinates: UMAP distorts global distance on purpose, so screen
 * proximity and semantic similarity are different questions.
 */
export async function fetchNeighbours(id: number, k = 5): Promise<Neighbour[]> {
  const res = await fetch(`/api/graph/similar/${id}?k=${k}`)
  if (!res.ok) return []
  return (await res.json()) as Neighbour[]
}
