/** Cluster detail and inter-cluster relationships, fetched on demand. */

export interface ClusterMember {
  paper_id: number
  title: string | null
  year: number | null
  first_author: string | null
  confidence: number | null
}

/**
 * A work to start with, and why.
 *
 * The reasons arrive as plain phrases from the server ("cited by most of the
 * region", "written first") — the UI shows them as chips and does not try to
 * be cleverer than the sentence it was handed.
 */
export interface EntryPoint {
  paper_id: number
  title: string | null
  year: number | null
  score: number
  reasons: string[]
}

export interface ClusterDetail {
  id: number
  label: string | null
  overview: string | null
  size: number
  terms: string[]
  members: ClusterMember[]
  /** Null while the region is too small or too even to have an obvious door. */
  entry_point: EntryPoint | null
  alternatives: EntryPoint[]
  /** Paper ids, in the order the region is best read. */
  reading_order: number[]
}

export interface ClusterLink {
  source_id: number
  target_id: number
  source_label: string | null
  target_label: string | null
  similarity: number
  shared_terms: string[]
  summary: string | null
  bridge_papers: ClusterMember[]
}

export async function fetchCluster(id: number): Promise<ClusterDetail> {
  const res = await fetch(`/api/clusters/${id}`)
  if (!res.ok) throw new Error(`cluster ${id} -> ${res.status}`)
  return (await res.json()) as ClusterDetail
}

export async function fetchClusterLinks(): Promise<ClusterLink[]> {
  const res = await fetch('/api/clusters')
  // No projection yet is a normal state, not an error worth surfacing.
  if (!res.ok) return []
  return (await res.json()) as ClusterLink[]
}
