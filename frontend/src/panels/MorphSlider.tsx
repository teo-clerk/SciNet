/**
 * Morph the map between two embedding models' opinions.
 *
 * Appears only when an alternate projection run exists (built with
 * scripts/build_alt_projection.py — the Model Lab documents it). Picking a
 * run fetches its positions once; the slider then lerps the point cloud in
 * place. The nodes that travel farthest are exactly the papers the two
 * embedders disagree about — a diagnostic no benchmark table can draw.
 */
import { useEffect, useState } from 'react'

import { fetchRunPositions, fetchRuns, type RunInfo } from '@/api/graph'
import { buildTarget, farthestTravellers } from '@/lib/morph'
import { useGraphStore } from '@/state/graphStore'

export function MorphSlider() {
  const runId = useGraphStore((s) => s.runId)
  const nodes = useGraphStore((s) => s.nodes)
  const buffers = useGraphStore((s) => s.buffers)
  const morphRunId = useGraphStore((s) => s.morphRunId)
  const morphT = useGraphStore((s) => s.morphT)
  const morphTarget = useGraphStore((s) => s.morphTarget)
  const morphBase = useGraphStore((s) => s.morphBase)
  const setMorph = useGraphStore((s) => s.setMorph)
  const setMorphT = useGraphStore((s) => s.setMorphT)
  const clearMorph = useGraphStore((s) => s.clearMorph)

  const [runs, setRuns] = useState<RunInfo[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let live = true
    void fetchRuns().then((all) => {
      if (live) setRuns(all.filter((r) => !r.is_active))
    })
    return () => {
      live = false
    }
  }, [runId])

  if (runs.length === 0) return null

  const pick = (id: number) => {
    if (!id || !buffers) {
      clearMorph()
      return
    }
    setLoading(true)
    void fetchRunPositions(id)
      .then((byId) => {
        // The base is copied once, at activation: the live position buffer
        // is about to be lerped in place, so "where everything was" has to
        // be preserved somewhere the lerp cannot touch.
        const base = morphBase ?? buffers.positions.slice()
        setMorph(id, buildTarget(nodes, base, byId), base)
      })
      .catch(() => clearMorph())
      .finally(() => setLoading(false))
  }

  const mover =
    morphTarget && morphBase && morphT > 0.4
      ? farthestTravellers(nodes, morphBase, morphTarget, 1)[0]
      : null

  return (
    <div className="morph" title="Morph between two embedding models' layouts">
      <select
        value={morphRunId ?? ''}
        onChange={(e) => pick(Number(e.target.value))}
      >
        <option value="">compare a run…</option>
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            run {r.id} · {r.model_id}
          </option>
        ))}
      </select>
      {morphRunId !== null && (
        <>
          <input
            type="range"
            min={0}
            max={100}
            value={Math.round(morphT * 100)}
            disabled={loading}
            aria-label="Blend between the two layouts"
            onChange={(e) => setMorphT(Number(e.target.value) / 100)}
          />
          <button className="clear" onClick={clearMorph} title="Back to the active layout">
            ×
          </button>
        </>
      )}
      {mover && (
        <span className="dim mover" title="The paper the two models disagree about most">
          top mover: {mover.title ?? `#${mover.id}`}
        </span>
      )}
    </div>
  )
}
