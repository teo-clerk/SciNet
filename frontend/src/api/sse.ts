/**
 * Live pipeline events.
 *
 * The stream is advisory: the worker publishes fire-and-forget and drops events
 * rather than blocking on a slow browser, so a missed event is normal and the
 * UI must never treat this as authoritative state. Counts come from /api/jobs.
 */
export interface PipelineEvent {
  kind: string
  at: number
  detail: Record<string, unknown>
}

export type EventHandler = (event: PipelineEvent) => void

const WATCHED = [
  'upload.received', 'upload.batch',
  'paper.added', 'parse.start', 'parse.done', 'metadata.done',
  'embed.done', 'project.done', 'tag.done', 'insight.done', 'job.failed',
  'paper.duplicate', 'paper.quarantined', 'paper.restored',
]

export function subscribeToEvents(onEvent: EventHandler): () => void {
  const source = new EventSource('/api/events')

  const listeners = WATCHED.map((kind) => {
    const handler = (message: MessageEvent) => {
      let detail: Record<string, unknown> = {}
      try {
        detail = JSON.parse(message.data)
      } catch {
        // A malformed frame is not worth breaking the drawer over.
      }
      onEvent({ kind, at: Date.now(), detail })
    }
    source.addEventListener(kind, handler as EventListener)
    return [kind, handler] as const
  })

  return () => {
    for (const [kind, handler] of listeners) {
      source.removeEventListener(kind, handler as EventListener)
    }
    source.close()
  }
}

export interface JobCounts {
  counts: Record<string, number>
  queued: number
  running: number
  failed: number
  dead: number
}

export async function fetchJobCounts(): Promise<JobCounts | null> {
  try {
    const res = await fetch('/api/jobs')
    if (!res.ok) return null
    return (await res.json()) as JobCounts
  } catch {
    return null
  }
}


export interface PauseState {
  paused: boolean
  reason?: string
  until?: string
  acked?: boolean
}

export async function fetchPauseState(): Promise<PauseState> {
  try {
    const res = await fetch('/api/jobs/pause')
    if (!res.ok) return { paused: false }
    return (await res.json()) as PauseState
  } catch {
    return { paused: false }
  }
}

export async function pauseWorker(): Promise<PauseState> {
  const res = await fetch('/api/jobs/pause', { method: 'POST' })
  if (!res.ok) return { paused: false }
  const body = (await res.json()) as { paused_until: string; reason: string }
  return { paused: true, reason: body.reason, until: body.paused_until }
}

export async function resumeWorker(): Promise<PauseState> {
  await fetch('/api/jobs/pause', { method: 'DELETE' })
  return { paused: false }
}
