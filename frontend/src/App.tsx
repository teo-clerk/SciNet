import { useEffect } from 'react'

import { Scene } from '@/graph/Scene'
import { makeMockGraph } from '@/lib/mockGraph'
import { StatusBar } from '@/panels/StatusBar'
import { useGraphStore } from '@/state/graphStore'

export default function App() {
  const setGraph = useGraphStore((s) => s.setGraph)

  useEffect(() => {
    // M0: synthetic corpus at target scale. Replaced by /api/graph at M3.
    const { buffers, meta } = makeMockGraph(4000)
    setGraph(buffers, meta)
  }, [setGraph])

  return (
    <div className="app">
      <StatusBar />
      <main className="canvas-host">
        <Scene />
      </main>
    </div>
  )
}
