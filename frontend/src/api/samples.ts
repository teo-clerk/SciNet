/**
 * Sample libraries, for someone with nothing to drop in yet.
 *
 * Two kinds. A `bundled` sample ships with the code and installs with one
 * click. A `fetch` sample names a command the reader runs themselves: the
 * application never downloads anything, and asking it to install one of these
 * is a 409 — the egress log stays empty by construction, not by promise.
 */

export interface Sample {
  name: string
  title: string
  blurb: string
  kind: 'bundled' | 'fetch'
  documents: number
  /** True once any of the sample's files is already in the library. */
  installed: boolean
  /** The shell command to run, for a `fetch` sample; null for a bundled one. */
  command: string | null
}

export interface InstallResult {
  name: string
  queued: number
  duplicates: number
}

export async function fetchSamples(): Promise<Sample[]> {
  const res = await fetch('/api/samples')
  if (!res.ok) throw new Error(`/api/samples -> ${res.status}`)
  return (await res.json()) as Sample[]
}

export async function installSample(name: string): Promise<InstallResult> {
  const res = await fetch(`/api/samples/${encodeURIComponent(name)}/install`, {
    method: 'POST',
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body?.detail
    throw new Error(
      typeof detail === 'string' ? detail : `could not install ${name} (${res.status})`,
    )
  }
  return (await res.json()) as InstallResult
}
