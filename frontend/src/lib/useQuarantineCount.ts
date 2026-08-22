/**
 * How many files the pipeline could not read.
 *
 * Polled once and then kept current from the event stream, because the worker
 * quarantines as it goes: during a five-hundred-file import the number changes
 * while the reader is looking at the screen, and a count that is only right at
 * page load is worse than no count at all — it says "nothing wrong" for the
 * whole of the run that went wrong.
 */
import { useEffect, useState } from 'react'

import { fetchQuarantine } from '@/api/graph'
import { subscribeToEvents } from '@/api/sse'

export function useQuarantineCount(): number {
  const [count, setCount] = useState(0)

  useEffect(() => {
    let live = true
    const reload = () => {
      fetchQuarantine()
        .then((page) => {
          if (live) setCount(page.total)
        })
        // A count is not worth an error banner; the tab simply stays hidden.
        .catch(() => undefined)
    }

    reload()
    const unsubscribe = subscribeToEvents((event) => {
      if (event.kind === 'paper.quarantined' || event.kind === 'paper.restored') {
        reload()
      }
    })

    return () => {
      live = false
      unsubscribe()
    }
  }, [])

  return count
}
