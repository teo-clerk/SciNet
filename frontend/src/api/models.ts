/**
 * The Model Lab's data: one GET carries hardware, catalog, discovery, pins,
 * per-task resolution, and live card residency. Mutations are tiny POSTs;
 * measurement is queued, not awaited — the worker runs it when the card is
 * free, and the panel's poll picks the result up.
 */

export interface HardwareInfo {
  vram_total_mib: number | null
  gpu_name: string | null
  ram_total_mib: number | null
  disk_free_gib: number | null
  vram_budget_mib: number
}

export interface ModelProfileInfo {
  reference: string
  runtime: string
  role: string
  disk_mib: number | null
  vram_mib: number | null
  tok_per_s: number | null
  embed_dim: number | null
  verdict: 'gpu' | 'partial' | 'cpu-only' | 'unproven' | string
  source: string
  notes: string | null
  fits_here: boolean | null
}

export interface DiscoveredInfo {
  reference: string
  server: string
  size_mib: number | null
  quantization: string | null
  in_catalog: boolean
}

export interface ModelsOverview {
  hardware: HardwareInfo
  profiles: ModelProfileInfo[]
  discovered: DiscoveredInfo[]
  pins: Record<string, string>
  tasks: Record<string, string>
  pinnable: string[]
  resident: string[]
}

export async function fetchModelsOverview(): Promise<ModelsOverview> {
  const res = await fetch('/api/models')
  if (!res.ok) throw new Error(`models endpoint answered ${res.status}`)
  return (await res.json()) as ModelsOverview
}

export async function requestMeasure(reference: string): Promise<void> {
  await fetch('/api/models/measure', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reference }),
  })
}

export async function setTaskPin(task: string, reference: string): Promise<void> {
  await fetch(`/api/models/pins/${task}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reference }),
  })
}

export async function clearTaskPin(task: string): Promise<void> {
  await fetch(`/api/models/pins/${task}`, { method: 'DELETE' })
}
