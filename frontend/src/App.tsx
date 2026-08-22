import { useEffect } from 'react'

import { fetchGraph } from '@/api/graph'
import { Scene } from '@/graph/Scene'
import { ClusterInspector } from '@/panels/ClusterInspector'
import { DetailPanel } from '@/panels/DetailPanel'
import { FilterBar } from '@/panels/FilterBar'
import { ListView } from '@/panels/ListView'
import { QuarantineView } from '@/panels/QuarantineView'
import { JobsDrawer } from '@/panels/JobsDrawer'
import { StatusBar } from '@/panels/StatusBar'
import { useGraphStore } from '@/state/graphStore'

export default function App() {
  const status = useGraphStore((s) => s.status)
  const view = useGraphStore((s) => s.view)
  const error = useGraphStore((s) => s.error)
  const setGraph = useGraphStore((s) => s.setGraph)
  const setLoading = useGraphStore((s) => s.setLoading)
  const setError = useGraphStore((s) => s.setError)

  useEffect(() => {
    setLoading()
    fetchGraph()
      .then(setGraph)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [setGraph, setLoading, setError])

  return (
    <div className="app">
      <StatusBar />
      {status === 'ready' && <FilterBar />}
      <main className="canvas-host">
        {status === 'ready' ? (
          view === 'map' ? (
            <Scene />
          ) : view === 'quarantine' ? (
            <QuarantineView />
          ) : (
            <ListView />
          )
        ) : (
          <div className="placeholder">
            {status === 'error' ? (
              <>
                <p className="warn">{error}</p>
                <p className="dim">
                  Start the worker: <code>python -m app.workers.runner</code>
                </p>
              </>
            ) : (
              <p className="dim">Loading the map…</p>
            )}
          </div>
        )}
        {/* Inside the canvas host, not beside it: anchored to the viewport
            they covered the search box and the filter bar, which are how the
            reader gets back out of whatever the panel is showing. */}
        <ClusterInspector />
        <DetailPanel />
      </main>
      <JobsDrawer />
    </div>
  )
}
