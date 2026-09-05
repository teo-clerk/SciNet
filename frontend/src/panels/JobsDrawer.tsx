/**
 * Live worker progress.
 *
 * Collapsed by default and pinned to a corner: ingestion runs for hours and
 * must not compete with the map for attention. It opens itself only when
 * something fails, which is the one case worth interrupting for.
 */
import { useEffect, useRef, useState } from 'react'

import {
  fetchJobCounts,
  fetchPauseState,
  pauseWorker,
  resumeWorker,
  subscribeToEvents,
  type JobCounts,
  type PauseState,
  type PipelineEvent,
} from '@/api/sse'
import { useGraphStore } from '@/state/graphStore'

const MAX_LOG = 40
const POLL_INTERVAL_MS = 4000

function describe(event: PipelineEvent): string {
  const d = event.detail
  switch (event.kind) {
    case 'upload.received':
      return `uploaded ${d.name}${d.outcome !== 'created' ? ` (${d.outcome})` : ''}`
    case 'upload.batch':
      return `batch: ${d.queued} queued, ${d.rejected} rejected`
    case 'paper.added':
      return `added ${d.name ?? d.paper_id}`
    case 'parse.start':
      return `parsing ${d.name ?? d.paper_id}`
    case 'parse.done':
      return `parsed #${d.paper_id} (tier ${d.tier}${d.degraded ? ', degraded' : ''})`
    case 'metadata.done':
      return `metadata #${d.paper_id}`
    case 'embed.done':
      return `embedded #${d.paper_id} (${d.chunks} chunks)`
    case 'project.done':
      return d.refitted
        ? `map refitted: ${d.total} papers, ${d.clusters} clusters`
        : `placed ${d.placed} new paper(s)`
    case 'tag.done':
      return `tagged #${d.paper_id}: ${(d.tags as string[] | undefined)?.join(', ') || 'none'}`
    case 'insight.done':
      return `core idea for #${d.paper_id}${d.grounded === false ? ' (thin evidence)' : ''}`
    case 'paper.duplicate':
      return `duplicate work key on #${d.paper_id}`
    case 'job.failed':
      return `FAILED ${d.job_kind} #${d.job_id}: ${String(d.error).slice(0, 80)}`
    default:
      return event.kind
  }
}

export function JobsDrawer() {
  const [open, setOpen] = useState(false)
  const [log, setLog] = useState<PipelineEvent[]>([])
  const [counts, setCounts] = useState<JobCounts | null>(null)
  const [pause, setPause] = useState<PauseState | null>(null)
  const labMode = useGraphStore((s) => s.labMode)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const unsubscribe = subscribeToEvents((event) => {
      setLog((prev) => [event, ...prev].slice(0, MAX_LOG))
      // A failure is the one event worth taking the viewer's attention for.
      if (event.kind === 'job.failed') setOpen(true)
    })
    return unsubscribe
  }, [])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      const next = await fetchJobCounts()
      const paused = await fetchPauseState()
      if (alive) {
        setCounts(next)
        setPause(paused)
      }
    }
    void tick()
    const timer = setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const outstanding = (counts?.queued ?? 0) + (counts?.running ?? 0)
  const busy = outstanding > 0
  const paused = pause?.paused ?? false

  const togglePause = async (event: React.MouseEvent) => {
    // The toggle sits inside the drawer's header button; stop the click
    // from also opening the log.
    event.stopPropagation()
    setPause(await (paused ? resumeWorker() : pauseWorker()))
  }

  // "X of Y" from the durable job table rather than from the event stream:
  // events are advisory and dropped under load, so counting them would drift.
  // Parse jobs are one per paper, which makes them the honest denominator.
  const parseDone = Object.entries(counts?.counts ?? {})
    .filter(([k]) => k.startsWith('parse:') && k.endsWith(':done'))
    .reduce((n, [, v]) => n + v, 0)
  const parseTotal = Object.entries(counts?.counts ?? {})
    .filter(([k]) => k.startsWith('parse:'))
    .reduce((n, [, v]) => n + v, 0)

  return (
    <div className={`jobs-drawer ${open ? 'open' : ''}`}>
      <button className="jobs-toggle" onClick={() => setOpen((v) => !v)}>
        <span className={`pulse ${paused ? 'paused' : busy ? 'busy' : ''}`} />
        {paused
          ? `paused (${pause?.reason ?? 'user'})`
          : busy
            ? parseTotal > 0
              ? `${parseDone} of ${parseTotal} papers · ${outstanding} jobs queued`
              : `${outstanding} queued`
            : 'pipeline idle'}
        <span
          className="pause-toggle"
          role="button"
          title={
            paused
              ? 'Resume the pipeline'
              : 'Pause the pipeline at the next job boundary'
          }
          onClick={togglePause}
        >
          {paused ? '▶' : '⏸'}
        </span>
        {counts?.dead ? <span className="warn"> · {counts.dead} dead</span> : null}
        <span className="chevron">{open ? '▾' : '▴'}</span>
      </button>

      {open && (
        <div className="jobs-body" ref={listRef}>
          {/* "12 of 40 papers" in the header is the sentence a reader needs;
              the per-kind breakdown is for whoever is watching the pipeline. */}
          {labMode && counts && (
            <div className="jobs-counts">
              {Object.entries(counts.counts)
                .filter(([key]) => !key.endsWith(':done'))
                .map(([key, n]) => (
                  <span key={key} className="chip">
                    {key.replace(':', ' ')} {n}
                  </span>
                ))}
            </div>
          )}
          {log.length === 0 ? (
            <p className="dim">No activity yet. Drop a PDF into the library.</p>
          ) : (
            log.map((event, i) => (
              <div
                key={`${event.at}-${i}`}
                className={event.kind === 'job.failed' ? 'row warn' : 'row'}
              >
                <span className="dim">
                  {new Date(event.at).toLocaleTimeString(undefined, { hour12: false })}
                </span>
                <span>{describe(event)}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
