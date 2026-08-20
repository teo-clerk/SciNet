/**
 * M0 instrumentation: proves the rendering budget and shows whether the backend
 * is reachable. Becomes the real status/job bar at M3.
 */
import { useEffect, useState } from 'react'

import { api, type SystemInfo } from '@/api/client'
import { useFps } from '@/lib/useFps'
import { useGraphStore } from '@/state/graphStore'

export function StatusBar() {
  const { fps, worstFrameMs } = useFps()
  const count = useGraphStore((s) => s.count)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)

  useEffect(() => {
    api
      .system()
      .then(setInfo)
      .catch((e: unknown) => setApiError(e instanceof Error ? e.message : String(e)))
  }, [])

  // 16.7 ms is the 60 fps budget; flag any window that blew through it.
  const budgetOk = worstFrameMs > 0 && worstFrameMs <= 16.7

  return (
    <div className="status-bar">
      <span className="brand">SciNet</span>
      <span>{count.toLocaleString()} nodes</span>
      <span className={fps >= 58 ? 'ok' : 'warn'}>{fps} fps</span>
      <span className={budgetOk ? 'ok' : 'warn'}>worst {worstFrameMs} ms</span>
      <span className="spacer" />
      {info ? (
        <>
          <span>{info.paper_count} papers</span>
          <span className={info.enrichment_enabled ? 'warn' : 'ok'}>
            {info.enrichment_enabled ? 'network: enrichment ON' : 'network: local only'}
          </span>
          <span className="dim">api v{info.version}</span>
        </>
      ) : (
        <span className="warn">api unreachable{apiError ? ` — ${apiError}` : ''}</span>
      )}
    </div>
  )
}
