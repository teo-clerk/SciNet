import { useCallback, useEffect } from 'react'

import { fetchGraph } from '@/api/graph'
import { Scene } from '@/graph/Scene'
import { ClusterInspector } from '@/panels/ClusterInspector'
import { DetailPanel } from '@/panels/DetailPanel'
import { DropOverlay } from '@/panels/DropOverlay'
import { EntryCard } from '@/panels/EntryCard'
import { FilterBar } from '@/panels/FilterBar'
import { LibrarianPanel } from '@/panels/LibrarianPanel'
import { ListView } from '@/panels/ListView'
import { ModelLab } from '@/panels/ModelLab'
import { QuantityReview } from '@/panels/QuantityReview'
import { QuarantineView } from '@/panels/QuarantineView'
import { JobsDrawer } from '@/panels/JobsDrawer'
import { LabDrawer } from '@/panels/LabDrawer'
import { McpModal } from '@/panels/McpModal'
import { RegionsBanner } from '@/panels/RegionsBanner'
import { StatusBar } from '@/panels/StatusBar'
import { TrailPanel } from '@/panels/TrailPanel'
import { Welcome } from '@/panels/Welcome'
import { useGraphStore } from '@/state/graphStore'

export default function App() {
  const status = useGraphStore((s) => s.status)
  const view = useGraphStore((s) => s.view)
  const setGraph = useGraphStore((s) => s.setGraph)
  const setLoading = useGraphStore((s) => s.setLoading)
  const setError = useGraphStore((s) => s.setError)

  // A function rather than a bare effect body, because the welcome screen
  // calls it too — when the first projection lands, and from its Retry.
  const loadGraph = useCallback(() => {
    setLoading()
    fetchGraph()
      .then(setGraph)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [setGraph, setLoading, setError])

  useEffect(() => {
    loadGraph()
  }, [loadGraph])

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
          ) : view === 'models' ? (
            <ModelLab />
          ) : view === 'review' ? (
            <QuantityReview />
          ) : (
            <ListView />
          )
        ) : (
          <Welcome reload={loadGraph} loading={status !== 'error'} />
        )}
        {status === 'ready' && <DropOverlay />}
        {status === 'ready' && <RegionsBanner />}
        {/* Inside the canvas host, not beside it: anchored to the viewport
            they covered the search box and the filter bar, which are how the
            reader gets back out of whatever the panel is showing. The entry
            card follows the inspector so the stylesheet can seat it beside
            the inspector when both are open. */}
        <ClusterInspector />
        <EntryCard />
        <DetailPanel />
        <LibrarianPanel />
        <TrailPanel />
      </main>
      <JobsDrawer />
      <LabDrawer />
      {/* Outside the canvas host on purpose: a dialog covers everything,
          including the welcome screen a fresh install shows. */}
      <McpModal />
    </div>
  )
}
