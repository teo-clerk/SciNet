/**
 * From one idea to another, through the library.
 *
 * Two ends — a paper or a phrase each — and the server walks the kNN graph
 * between them. The stops come back named, so the panel can say what each
 * step is about instead of leaving the reader to guess from titles; the map
 * draws the same stops as a numbered line. Clicking a stop flies to it; the
 * tour does the clicking on a timer.
 *
 * Bottom-centre, the librarian's spot, and the two are mutually exclusive:
 * both are answers about the map and want to sit under it, and the store
 * closes one when the other opens.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchPath } from '@/api/trails'
import { frameForSet } from '@/lib/framing'
import { prefersReducedMotion } from '@/lib/motion'
import { EngineWarming } from '@/lib/search'
import { endIsSet, stopsToIndices, tourStep, TOUR_STEP_MS, type TrailEnd } from '@/lib/trail'
import { EndpointPicker } from '@/panels/EndpointPicker'
import { TrailStops } from '@/panels/TrailStops'
import { useGraphStore } from '@/state/graphStore'

/** As the search box: long enough for the model to make progress, short
 *  enough that the trail appears the moment the engine is ready. */
const RETRY_MS = 1500
const EMPTY_END: TrailEnd = { paperId: null, text: '' }

export function TrailPanel() {
  const open = useGraphStore((s) => s.trailOpen)
  if (!open) return null
  return <TrailPanelBody />
}

function TrailPanelBody() {
  const toggle = useGraphStore((s) => s.toggleTrailPanel)
  const nodes = useGraphStore((s) => s.nodes)
  const buffers = useGraphStore((s) => s.buffers)
  const stops = useGraphStore((s) => s.trailStops)
  const complete = useGraphStore((s) => s.trailComplete)
  const cursor = useGraphStore((s) => s.trailCursor)
  const mode = useGraphStore((s) => s.trailMode)
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const setSelected = useGraphStore((s) => s.setSelected)
  const setView = useGraphStore((s) => s.setView)
  const setTrailResult = useGraphStore((s) => s.setTrailResult)
  const setTrailCursor = useGraphStore((s) => s.setTrailCursor)
  const clearTrail = useGraphStore((s) => s.clearTrail)
  const flyToPoint = useGraphStore((s) => s.flyToPoint)

  const [from, setFrom] = useState<TrailEnd>(EMPTY_END)
  const [to, setTo] = useState<TrailEnd>(EMPTY_END)
  /** Paper ids the last request anchored each typed phrase to. */
  const [anchored, setAnchored] = useState<{ from: number | null; to: number | null }>({
    from: null,
    to: null,
  })
  const [finding, setFinding] = useState(false)
  const [warming, setWarming] = useState<{ remaining: number | null } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [touring, setTouring] = useState(false)
  const inFlight = useRef<AbortController | null>(null)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const still = useRef(prefersReducedMotion())

  const indices = useMemo(() => (stops ? stopsToIndices(stops, nodes) : []), [stops, nodes])
  const titleOf = (id: number | null) =>
    id === null ? null : (nodes.find((n) => n.id === id)?.title ?? `#${id}`)

  useEffect(
    () => () => {
      inFlight.current?.abort()
      if (retryTimer.current) clearTimeout(retryTimer.current)
    },
    [],
  )

  const visit = useCallback(
    (i: number) => {
      const index = indices[i]
      if (index === undefined) return
      setView('map') // the flight should be watched, not missed behind a table
      setTrailCursor(i)
      setSelected(index)
    },
    [indices, setView, setTrailCursor, setSelected],
  )

  // The tour: one stop per tick until the last, then it stops itself. The
  // cursor is read from the store at each tick rather than closed over, so a
  // click on a stop mid-tour moves the tour too.
  useEffect(() => {
    if (!touring) return
    const timer = setInterval(() => {
      const next = tourStep(useGraphStore.getState().trailCursor, indices.length)
      if (next === null) setTouring(false)
      else visit(next)
    }, TOUR_STEP_MS)
    return () => clearInterval(timer)
  }, [touring, indices.length, visit])

  const startTour = () => {
    const first = tourStep(null, indices.length)
    if (first === null) return
    visit(first)
    setTouring(true)
  }

  const find = useCallback(
    (retried: boolean) => {
      if (!endIsSet(from) || !endIsSet(to)) return
      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller
      setFinding(true)
      setError(null)
      setTouring(false)

      fetchPath(from, to, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return
          setWarming(null)
          setAnchored({
            from: result.from.anchored_from_text === null ? null : result.from.paper_id,
            to: result.to.anchored_from_text === null ? null : result.to.paper_id,
          })
          setView('map')
          setTrailResult(result.stops, result.complete, 'path')
          // Frame the whole trail: a path found off-screen is a path not seen.
          if (buffers) {
            const framing = frameForSet(stopsToIndices(result.stops, nodes), buffers.positions)
            if (framing) flyToPoint(framing.target, framing.distance)
          }
          setFinding(false)
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return
          if (e instanceof EngineWarming) {
            // Not a failure: the model is loading. Say so, and ask once more
            // by itself before leaving the button to the reader.
            setWarming({ remaining: e.estimatedRemaining })
            if (!retried) {
              retryTimer.current = setTimeout(() => find(true), RETRY_MS)
              return
            }
            setFinding(false)
            return
          }
          setWarming(null)
          setError(e instanceof Error ? e.message : String(e))
          setFinding(false)
        })
    },
    [from, to, nodes, buffers, setView, setTrailResult, flyToPoint],
  )

  const takeSelected = (setEnd: (end: TrailEnd) => void) => {
    const node = selectedIndex === null ? undefined : nodes[selectedIndex]
    if (node) setEnd({ paperId: node.id, text: '' })
  }

  const readingOrder = mode === 'reading-order'
  const hasStops = stops !== null && stops.length > 0

  return (
    <aside className="trail-panel">
      <header>
        <h3>{readingOrder ? 'Reading order' : '✧ Idea trail'}</h3>
        <button className="close" aria-label="Close" onClick={toggle}>
          ×
        </button>
      </header>

      {!readingOrder && (
        <form
          className="endpoints"
          onSubmit={(event) => {
            event.preventDefault()
            find(false)
          }}
        >
          <EndpointPicker
            label="Start"
            end={from}
            onChange={(end) => {
              setFrom(end)
              setAnchored((a) => ({ ...a, from: null }))
            }}
            chosenTitle={titleOf(from.paperId)}
            anchoredTitle={titleOf(anchored.from)}
            canUseSelected={selectedIndex !== null}
            onUseSelected={() => takeSelected(setFrom)}
            disabled={finding}
          />
          <EndpointPicker
            label="Destination"
            end={to}
            onChange={(end) => {
              setTo(end)
              setAnchored((a) => ({ ...a, to: null }))
            }}
            chosenTitle={titleOf(to.paperId)}
            anchoredTitle={titleOf(anchored.to)}
            canUseSelected={selectedIndex !== null}
            onUseSelected={() => takeSelected(setTo)}
            disabled={finding}
          />
          <div className="row">
            <button type="submit" disabled={finding || !endIsSet(from) || !endIsSet(to)}>
              {finding ? 'Finding…' : 'Find a path'}
            </button>
            {warming && (
              <span className="dim status">
                The meaning engine is still starting
                {warming.remaining !== null && warming.remaining > 1
                  ? ` — about ${Math.ceil(warming.remaining)} s…`
                  : '…'}
              </span>
            )}
            {error && <span className="warn status">{error}</span>}
          </div>
        </form>
      )}

      {!hasStops && !readingOrder && (
        <p className="dim hint">
          Pick two ends — a paper on the map, or an idea in your own words — and the
          trail walks the library from one to the other.
        </p>
      )}

      {hasStops && (
        <>
          <TrailStops stops={stops} complete={complete} cursor={cursor} onVisit={visit} />
          <div className="row controls">
            {touring ? (
              <button type="button" onClick={() => setTouring(false)}>
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={startTour}
                disabled={still.current || indices.length < 2}
                title={
                  still.current
                    ? 'Reduced motion is on — step through by clicking the stops'
                    : 'Fly from stop to stop'
                }
              >
                Tour
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setTouring(false)
                clearTrail()
              }}
            >
              Clear
            </button>
          </div>
        </>
      )}
    </aside>
  )
}
