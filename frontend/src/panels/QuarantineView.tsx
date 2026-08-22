/**
 * The documents the pipeline could not read.
 *
 * These used to be invisible. A file that cannot be parsed simply never became
 * a node, so the only symptom was a paper count lower than the file count, and
 * finding out which files and why meant reading the worker's log. For a library
 * of five hundred that is not a diagnosis anyone performs.
 *
 * Its own view rather than rows in the list: a quarantined file has no title,
 * no year, no region and no position, so it would be a row where every column
 * the list exists to show is blank. What it has instead is a filename and a
 * reason, and those are what this shows.
 */
import { useCallback, useEffect, useState } from 'react'

import {
  fetchQuarantine,
  restorePaper,
  type QuarantinedPaper,
} from '@/api/graph'
import { subscribeToEvents } from '@/api/sse'

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

export function QuarantineView() {
  const [items, setItems] = useState<QuarantinedPaper[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  const reload = useCallback(() => {
    fetchQuarantine()
      .then((page) => {
        setItems(page.items)
        setError(null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(() => {
    reload()
    // The worker quarantines as it goes, so a list fetched once goes stale
    // while the reader is looking at it.
    return subscribeToEvents((event) => {
      if (event.kind === 'paper.quarantined' || event.kind === 'paper.restored') {
        reload()
      }
    })
  }, [reload])

  const putBack = async (paper: QuarantinedPaper) => {
    setBusy(paper.id)
    try {
      await restorePaper(paper.id)
      reload()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  if (error) {
    return (
      <div className="quarantine-view empty">
        <p className="warn">{error}</p>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="quarantine-view empty">
        <p className="dim">
          Nothing has been quarantined. Every file in the library was readable.
        </p>
      </div>
    )
  }

  return (
    <div className="quarantine-view">
      <p className="quarantine-intro dim">
        {items.length} file{items.length === 1 ? '' : 's'} could not be read and
        {items.length === 1 ? ' was' : ' were'} moved out of the library to{' '}
        <code>data/quarantine/</code>. Nothing was deleted — restoring one puts
        it back and queues it again.
      </p>

      <table className="quarantine-table">
        <thead>
          <tr>
            <th>File</th>
            <th>Why</th>
            <th>When</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((paper) => (
            <tr key={paper.id}>
              <td className="q-name" title={paper.filename}>
                {paper.filename}
              </td>
              <td className="q-reason">
                <span className={paper.fatal ? 'badge fatal' : 'badge transient'}>
                  {paper.fatal ? 'unreadable' : 'gave up'}
                </span>
                {paper.reason}
              </td>
              <td className="q-when dim">{when(paper.quarantined_at)}</td>
              <td>
                <button
                  className="ghost"
                  disabled={busy === paper.id}
                  onClick={() => void putBack(paper)}
                  // A file is here on one parser's verdict, which is a weaker
                  // claim than the content checks made before ingestion.
                  title="Move it back to the library and parse it again"
                >
                  {busy === paper.id ? 'Restoring…' : 'Restore'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
