/**
 * The two things a reader can ask of the map itself.
 *
 * Kept out of the filter row's mode buttons on purpose: those change what the
 * map *shows*, and these change what it *is* — one moves the camera, the other
 * rebuilds the projection. Grouping them with the colour modes would suggest
 * the rebuild is as cheap and as reversible as switching to year colouring.
 */
import { useCallback, useEffect, useState } from 'react'

import { requestReprojection } from '@/api/graph'
import { subscribeToEvents } from '@/api/sse'
import { useGraphStore } from '@/state/graphStore'

type RefitState = 'idle' | 'asking' | 'running' | 'failed'

/** How long the finished notice stays up before the button returns to normal. */
const SETTLE_MS = 6000

export function MapControls() {
  const autoRotate = useGraphStore((s) => s.autoRotate)
  const toggleAutoRotate = useGraphStore((s) => s.toggleAutoRotate)
  const [refit, setRefit] = useState<RefitState>('idle')
  const [note, setNote] = useState<string | null>(null)

  // The worker announces when it lands. Without this the button has no way to
  // tell the reader that anything happened: a refit takes tens of seconds and
  // a control that goes quiet for that long reads as broken.
  useEffect(() => {
    return subscribeToEvents((event) => {
      if (event.kind !== 'project.done') return
      const clusters = event.detail.clusters
      setRefit('idle')
      setNote(
        typeof clusters === 'number'
          ? `Rebuilt — ${clusters} regions. Reload to see it.`
          : 'Rebuilt. Reload to see it.',
      )
      setTimeout(() => setNote(null), SETTLE_MS)
    })
  }, [])

  const rebuild = useCallback(async () => {
    setRefit('asking')
    setNote(null)
    try {
      const { already_queued } = await requestReprojection()
      setRefit('running')
      setNote(
        already_queued
          ? 'Already under way.'
          : 'Queued. The worker has to be running.',
      )
    } catch (error: unknown) {
      setRefit('failed')
      setNote(error instanceof Error ? error.message : String(error))
    }
  }, [])

  return (
    <div className="map-controls">
      <button
        className={autoRotate ? 'active' : ''}
        onClick={toggleAutoRotate}
        aria-pressed={autoRotate}
        title="Turn the map slowly, hands-off"
      >
        {autoRotate ? '⏸ Rotating' : '⟳ Auto-rotate'}
      </button>

      <button
        onClick={() => void rebuild()}
        disabled={refit === 'asking' || refit === 'running'}
        title="Recompute the layout, clusters and bridges over every paper"
      >
        {refit === 'running' ? 'Rebuilding…' : '↻ Rebuild map'}
      </button>

      {note && (
        <span className={refit === 'failed' ? 'control-note warn' : 'control-note dim'}>
          {note}
        </span>
      )}
    </div>
  )
}
