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

/** Radius the map is normalised to, so camera, fog and point sizes stay tuned. */
export const CANONICAL_RADIUS = 40
/** Share of papers that must fall inside CANONICAL_RADIUS. */
const BULK_PERCENTILE = 0.9

/**
 * Centre the layout on the origin and scale it to a known size.
 *
 * UMAP emits whatever range it likes — a real 293-paper run came out spanning
 * x[6.1, 9.8], y[1.9, 8.1], nowhere near the origin the camera points at, and
 * small enough to be a speck at the default distance. Every tuned constant in
 * the renderer (camera distance, fog near/far, point size, label offsets)
 * assumes a corpus of roughly known extent, so the normalisation happens once
 * here rather than being compensated for in five places.
 *
 * This is display-only. The stored coordinates stay exactly as fitted, because
 * Procrustes alignment across refits depends on them.
 */
export function normalisePositions(positions: Float32Array): Float32Array {
  const n = positions.length / 3
  if (n === 0) return positions

  let cx = 0, cy = 0, cz = 0
  for (let i = 0; i < n; i++) {
    cx += positions[i * 3]!
    cy += positions[i * 3 + 1]!
    cz += positions[i * 3 + 2]!
  }
  cx /= n; cy /= n; cz /= n

  // Scale by a high percentile rather than the maximum. UMAP routinely strands
  // a few papers far from the bulk, and normalising by the furthest one shrinks
  // the whole corpus into a speck to make room for three outliers. The tail is
  // allowed to fall outside the canonical radius; the camera still frames what
  // the viewer came to look at.
  const radii = new Float64Array(n)
  for (let i = 0; i < n; i++) {
    const dx = positions[i * 3]! - cx
    const dy = positions[i * 3 + 1]! - cy
    const dz = positions[i * 3 + 2]! - cz
    radii[i] = Math.sqrt(dx * dx + dy * dy + dz * dz)
  }
  const sorted = Float64Array.from(radii).sort()
  const reference = sorted[Math.min(n - 1, Math.floor(n * BULK_PERCENTILE))] ?? 0
  const scale = reference > 1e-6 ? CANONICAL_RADIUS / reference : 1

  const out = new Float32Array(positions.length)
  for (let i = 0; i < n; i++) {
    out[i * 3] = (positions[i * 3]! - cx) * scale
    out[i * 3 + 1] = (positions[i * 3 + 1]! - cy) * scale
    out[i * 3 + 2] = (positions[i * 3 + 2]! - cz) * scale
  }
  return out
}

export function decodeGraph(payload: GraphPayload): DecodedGraph {
  const raw = base64ToFloat32(payload.positions_f32)
  if (raw.length !== payload.count * 3) {
    throw new Error(
      `graph payload is inconsistent: ${raw.length / 3} positions for ` +
        `${payload.count} nodes`,
    )
  }
  const positions = normalisePositions(raw)
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
