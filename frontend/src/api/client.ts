/** Thin fetch wrapper. Same-origin in dev via the Vite proxy. */

export interface SystemInfo {
  version: string
  pipeline_version: number
  paths: { library_dir: string; markdown_dir: string; db_path: string }
  embed_model: string
  llm_model: string
  vlm_model: string
  device: string
  enrichment_enabled: boolean
  paper_count: number
  ready_count: number
  /** The search embedder's state: cold, warming, ready or failed. */
  search_warmup: string
  /** Seconds until warm, when the warmer can estimate it. */
  search_warmup_remaining: number | null
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export const api = {
  health: () => getJson<{ status: string; version: string }>('/api/health'),
  system: () => getJson<SystemInfo>('/api/system'),
}
