/**
 * Scrub the library through time.
 *
 * The cutoff rides the same visible-set machinery as search and tags, so
 * papers past it ghost to the filtered look rather than vanishing — the
 * shape of what is coming stays faintly present, which is what makes the
 * play-through read as fields *emerging* instead of popping in. Undated
 * papers stay visible at every position: an unknown year is not a "later"
 * year.
 *
 * Dragging the slider to its right edge releases the filter entirely (null),
 * so the resting state never claims to be filtering while showing everything.
 */
import { useEffect, useMemo, useRef, useState } from 'react'

import { useGraphStore } from '@/state/graphStore'

/** One pass over the whole timeline. Long enough to watch fields arrive. */
const PLAY_MS = 8000

export function TimeScrubber() {
  const nodes = useGraphStore((s) => s.nodes)
  const yearCutoff = useGraphStore((s) => s.yearCutoff)
  const setYearCutoff = useGraphStore((s) => s.setYearCutoff)
  const [playing, setPlaying] = useState(false)
  const raf = useRef<number | null>(null)

  const range = useMemo(() => {
    let min = Infinity
    let max = -Infinity
    for (const node of nodes) {
      if (node.year === null) continue
      if (node.year < min) min = node.year
      if (node.year > max) max = node.year
    }
    return max > min ? { min, max } : null
  }, [nodes])

  useEffect(() => {
    if (!playing || !range) return
    const start = performance.now()
    const span = range.max - range.min
    const tick = (now: number) => {
      const t = Math.min((now - start) / PLAY_MS, 1)
      setYearCutoff(Math.round(range.min + span * t))
      if (t >= 1) {
        // The story told, the filter releases.
        setPlaying(false)
        setYearCutoff(null)
        return
      }
      raf.current = requestAnimationFrame(tick)
    }
    raf.current = requestAnimationFrame(tick)
    return () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current)
    }
  }, [playing, range, setYearCutoff])

  // A corpus with one publication year (or none) has no timeline to tell.
  if (!range) return null

  const value = yearCutoff ?? range.max
  return (
    <div className="scrubber">
      <button
        className={playing ? 'active' : ''}
        onClick={() => setPlaying((p) => !p)}
        aria-label={playing ? 'Pause the timeline' : 'Play the timeline'}
        title="Watch the library grow year by year"
      >
        {playing ? '⏸' : '▶'}
      </button>
      <input
        type="range"
        min={range.min}
        max={range.max}
        value={value}
        aria-label="Show papers up to this year"
        onChange={(event) => {
          setPlaying(false)
          const year = Number(event.target.value)
          setYearCutoff(year >= range.max ? null : year)
        }}
      />
      <span className={yearCutoff !== null ? 'year active' : 'year'}>{value}</span>
    </div>
  )
}
