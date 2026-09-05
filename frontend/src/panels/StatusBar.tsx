/**
 * The top line: what is on the map, what is in the library, and whether
 * anything leaves this machine.
 *
 * The privacy badge is always shown — it is a fact about the software, not a
 * diagnostic. The frame timing, the run id and the API version are the numbers
 * behind the map and belong to the Lab, which the last button turns on.
 */
import { useEffect, useState } from 'react'

import { api, type SystemInfo } from '@/api/client'
import { FRAME_BUDGET_MS, useFps } from '@/lib/useFps'
import { useGraphStore } from '@/state/graphStore'

export function StatusBar() {
  const count = useGraphStore((s) => s.count)
  const method = useGraphStore((s) => s.method)
  const runId = useGraphStore((s) => s.runId)
  const labMode = useGraphStore((s) => s.labMode)
  const setLabMode = useGraphStore((s) => s.setLabMode)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)

  useEffect(() => {
    api
      .system()
      .then(setInfo)
      .catch((e: unknown) => setApiError(e instanceof Error ? e.message : String(e)))
  }, [])

  return (
    <div className="status-bar">
      <span className="brand">SciNet</span>
      <span>{count.toLocaleString()} works on the map</span>
      {labMode && method && (
        <span className="dim">
          run {runId} · {method}
        </span>
      )}
      {labMode && <FrameTiming />}
      <span className="spacer" />
      {info ? (
        <>
          <span>{info.paper_count.toLocaleString()} in the library</span>
          <span className={info.enrichment_enabled ? 'warn' : 'ok'}>
            {info.enrichment_enabled ? 'network: enrichment ON' : 'nothing leaves this machine'}
          </span>
          {labMode && <span className="dim">api v{info.version}</span>}
        </>
      ) : (
        <span className="warn">api unreachable{apiError ? ` — ${apiError}` : ''}</span>
      )}
      <button
        className="lab-toggle"
        aria-pressed={labMode}
        title="Show the numbers behind the map"
        onClick={() => setLabMode(!labMode)}
      >
        ⚗ Lab
      </button>
    </div>
  )
}

/** Mounted only in lab mode: the sampler runs a requestAnimationFrame loop,
 *  and there is no reason to pay for a meter nobody can see. */
function FrameTiming() {
  const { fps, worstFrameMs } = useFps()
  const budgetOk = worstFrameMs > 0 && worstFrameMs <= FRAME_BUDGET_MS

  return (
    <>
      <span className={fps >= 58 ? 'ok' : 'warn'}>{fps} fps</span>
      <span className={budgetOk ? 'ok' : 'warn'}>worst {worstFrameMs} ms</span>
    </>
  )
}
