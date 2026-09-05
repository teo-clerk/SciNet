/**
 * The stops of a trail, numbered the way the map numbers them.
 *
 * Each row carries its caption — position, year, region — the title, and the
 * work's central question when the INSIGHT stage has written one, so the
 * reader can see what a step is about without opening it. The last row of an
 * incomplete path is marked as the leap it is.
 */
import { hopIsJump, stopCaption, type TrailStop } from '@/lib/trail'

interface Props {
  stops: TrailStop[]
  complete: boolean
  cursor: number | null
  onVisit: (index: number) => void
}

export function TrailStops({ stops, complete, cursor, onVisit }: Props) {
  return (
    <ol className="stops">
      {stops.map((stop, i) => {
        const jump = hopIsJump(i, stops.length, complete)
        const classes = ['stop', i === cursor ? 'current' : '', jump ? 'jump' : '']
        return (
          <li key={`${stop.paper_id}-${i}`} className={classes.filter(Boolean).join(' ')}>
            <button type="button" onClick={() => onVisit(i)}>
              <span className="caption dim">{stopCaption(stop, i + 1)}</span>
              <span className="title">{stop.title ?? `#${stop.paper_id}`}</span>
              {stop.core_question && <span className="prose question">{stop.core_question}</span>}
            </button>
            {jump && (
              <p className="dim small">
                the library has no continuous chain here — this last step is a jump
              </p>
            )}
          </li>
        )
      })}
    </ol>
  )
}
