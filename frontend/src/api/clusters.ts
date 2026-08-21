/** Cluster detail and inter-cluster relationships, fetched on demand. */

export interface ClusterMember {
  paper_id: number
  title: string | null
  year: number | null
  first_author: string | null
  confidence: number | null
}

export interface ClusterDetail {
  id: number
  label: string | null
  overview: string | null
  size: number
  terms: string[]
  members: ClusterMember[]
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
