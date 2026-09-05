/**
 * The numbers behind the map, for whoever wants them.
 *
 * Off by default. A reader looking for a book does not need the frame time,
 * the projection run's id or how many embed jobs are queued; a developer
 * measuring a change needs all of them at once. Giving them a drawer of their
 * own is what lets the panels the reader uses speak plainly.
 *
 * Mounted only while the lab is on, so its polling and its frame sampler cost
 * nothing the rest of the time.
 */
import { useEffect, useState } from 'react'

import { api, type SystemInfo } from '@/api/client'
import { fetchRuns, type RunInfo } from '@/api/graph'
import { fetchJobCounts, type JobCounts } from '@/api/sse'
import { FRAME_BUDGET_MS, useFps } from '@/lib/useFps'
import { useGraphStore } from '@/state/graphStore'

/** Same cadence as the jobs drawer; the durable job table is cheap to count. */
const POLL_INTERVAL_MS = 4000

export function LabDrawer() {
  const labMode = useGraphStore((s) => s.labMode)
  if (!labMode) return null
  return <LabDrawerBody />
}

function LabDrawerBody() {
  const setLabMode = useGraphStore((s) => s.setLabMode)
  const setView = useGraphStore((s) => s.setView)
  const runId = useGraphStore((s) => s.runId)
  const { fps, worstFrameMs } = useFps()
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [counts, setCounts] = useState<JobCounts | null>(null)
  const [system, setSystem] = useState<SystemInfo | null>(null)

  // Re-fetched when the active run changes: a refit lands as a new run id.
  useEffect(() => {
    let alive = true
    void fetchRuns().then((r) => alive && setRuns(r))
    return () => {
      alive = false
    }
  }, [runId])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      const [next, info] = await Promise.all([
        fetchJobCounts(),
        api.system().catch(() => null),
      ])
      if (!alive) return
      setCounts(next)
      setSystem(info)
    }
    void tick()
    const timer = setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const active = runs.find((r) => r.is_active) ?? runs.find((r) => r.id === runId) ?? null
  const budgetOk = worstFrameMs > 0 && worstFrameMs <= FRAME_BUDGET_MS
  const warmup = system?.search_warmup ?? null
  const remaining = system?.search_warmup_remaining ?? null

  return (
    <aside className="lab-drawer">
      <header>
        <h3>⚗ Lab</h3>
        <button className="close" onClick={() => setLabMode(false)} aria-label="Close the lab">
          ×
        </button>
      </header>

      <dl>
        <dt>Frame</dt>
        <dd>
          <span className={fps >= 58 ? 'ok' : 'warn'}>{fps} fps</span>
          <span className={budgetOk ? 'ok' : 'warn'}>worst {worstFrameMs} ms</span>
          <span className="dim">{budgetOk ? 'within budget' : `budget ${FRAME_BUDGET_MS} ms`}</span>
        </dd>

        <dt>Run</dt>
        <dd>
          {active ? (
            <>
              <span>#{active.id}</span>
              <span className="dim">{active.model_id}</span>
              <span className="dim">{active.method}</span>
              {active.n_fit !== null && <span className="dim">fit on {active.n_fit}</span>}
              {active.fitted_at && (
                <span className="dim">{new Date(active.fitted_at).toLocaleString()}</span>
              )}
            </>
          ) : (
            <span className="dim">no projection yet</span>
          )}
        </dd>

        <dt>Jobs</dt>
        <dd className="jobs-counts">
          {counts && Object.keys(counts.counts).length > 0 ? (
            Object.entries(counts.counts).map(([key, n]) => (
              <span key={key} className="chip">
                {key.replace(':', ' ')} {n}
              </span>
            ))
          ) : (
            <span className="dim">{counts ? 'queue empty' : 'unreachable'}</span>
          )}
        </dd>

        <dt>Search</dt>
        <dd>
          <span className={warmup === 'ready' ? 'ok' : warmup === 'failed' ? 'warn' : 'dim'}>
            {warmup ?? 'unknown'}
          </span>
          {remaining !== null && warmup === 'warming' && (
            <span className="dim">~{Math.ceil(remaining)} s</span>
          )}
        </dd>
      </dl>

      <button className="open-models" onClick={() => setView('models')}>
        Open the Model Lab
      </button>
    </aside>
  )
}
