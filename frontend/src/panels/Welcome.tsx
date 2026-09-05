/**
 * What the reader sees when there is no map to show.
 *
 * Four causes, told apart by watching rather than by one look (lib/welcome):
 * the API is not running, the library is empty, the worker is still reading
 * it, or jobs are queued and nothing is taking them. Each gets its own
 * sentence and its own action. The whole card is a drop target, so the
 * shortest path from a blank screen to a map is dragging a folder onto it.
 *
 * Polls system and job counts on the drawer's cadence and listens to the
 * event stream: when the first projection lands, it reloads the graph and
 * turns into the map by itself.
 */
import { useEffect, useRef, useState } from 'react'

import { api } from '@/api/client'
import { fetchJobCounts, subscribeToEvents } from '@/api/sse'
import { ACCEPTED } from '@/lib/upload'
import { useDropZone } from '@/lib/useDropZone'
import { useUploader } from '@/lib/useUploader'
import {
  countParseDone,
  deriveWelcome,
  nextIdlePolls,
  splitCode,
  welcomeCopy,
  type WelcomeState,
} from '@/lib/welcome'
import { SampleCards } from '@/panels/SampleCards'
import { UploadSummary } from '@/panels/UploadSummary'
import { useGraphStore } from '@/state/graphStore'

/** Same cadence as the jobs drawer; the durable job table is cheap to count. */
const POLL_INTERVAL_MS = 4000

interface Props {
  /** App's graph loader — the way out of this screen. */
  reload: () => void
  /** True while a load is in flight; the poll result waits behind it. */
  loading: boolean
}

export function Welcome({ reload, loading }: Props) {
  const labMode = useGraphStore((s) => s.labMode)
  const loadError = useGraphStore((s) => s.error)
  const [state, setState] = useState<WelcomeState | null>(null)
  const [queued, setQueued] = useState(0)
  const idlePolls = useRef(0)
  const input = useRef<HTMLInputElement>(null)
  const { progress, busy, send } = useUploader()
  const { dragging, dropProps } = useDropZone(send)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      const [system, counts] = await Promise.all([
        api.system().catch(() => null),
        fetchJobCounts(),
      ])
      if (!alive) return
      const queuedNow = counts?.queued ?? 0
      idlePolls.current = nextIdlePolls(idlePolls.current, queuedNow, counts?.running ?? 0)
      setQueued(queuedNow)
      setState(
        deriveWelcome({
          apiReachable: system !== null,
          paperCount: system?.paper_count ?? null,
          queued: counts?.queued ?? null,
          running: counts?.running ?? null,
          parseDone: counts ? countParseDone(counts.counts) : null,
          idleQueuePolls: idlePolls.current,
        }),
      )
    }
    void tick()
    const timer = setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  // The first projection is the event this screen exists to wait for.
  useEffect(
    () =>
      subscribeToEvents((event) => {
        if (event.kind === 'project.done') reload()
      }),
    [reload],
  )

  if (loading || state === null) {
    return (
      <div className="welcome">
        <p className="dim">Loading the map…</p>
      </div>
    )
  }

  const copy = welcomeCopy(state, queued)
  const canRetry = state.kind === 'api-down' || state.kind === 'worker-down'
  const percent =
    state.progress && state.progress.total > 0
      ? Math.min(100, Math.round((100 * state.progress.done) / state.progress.total))
      : null

  return (
    <div className={dragging ? 'welcome dropping' : 'welcome'} {...dropProps}>
      <h1>{copy.heading}</h1>
      <p className="prose">
        {splitCode(copy.body).map((part, i) =>
          part.code ? <code key={i}>{part.text}</code> : <span key={i}>{part.text}</span>,
        )}
      </p>
      {labMode && loadError && <p className="dim small">{loadError}</p>}

      {canRetry && (
        <button className="retry" onClick={reload}>
          Retry
        </button>
      )}

      {state.kind === 'building' && (
        <div
          className="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent ?? undefined}
        >
          <span
            className={percent === null ? 'fill indeterminate' : 'fill'}
            style={percent === null ? undefined : { width: `${percent}%` }}
          />
        </div>
      )}

      {state.kind === 'empty' && (
        <>
          <div className={dragging ? 'drop-zone dropping' : 'drop-zone'}>
            <span>Drop files here</span>
            <button type="button" onClick={() => input.current?.click()} disabled={busy}>
              or choose files
            </button>
          </div>
          <SampleCards />
        </>
      )}

      {progress && (
        <p className="upload-line">
          <UploadSummary progress={progress} busy={busy} />
        </p>
      )}

      <input
        ref={input}
        type="file"
        accept={ACCEPTED.join(',')}
        multiple
        hidden
        onChange={(event) => {
          void send(Array.from(event.target.files ?? []))
          event.target.value = '' // allow re-picking the same files
        }}
      />
    </div>
  )
}
