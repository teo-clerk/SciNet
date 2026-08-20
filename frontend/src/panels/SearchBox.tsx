/**
 * The search box, with its three modes made explicit.
 *
 * The mode is a visible choice rather than a heuristic. Blending exact and
 * semantic results into one ranked list would hide which kind of match
 * produced a hit — and that distinction is precisely what tells the reader
 * whether to trust it. Semantic search always returns its k nearest, however
 * unrelated; presented alongside exact matches without a label, those read as
 * false positives from a broken search.
 */
import { useCallback, useEffect, useRef } from 'react'

import { buildTitleIndex, searchServer, searchTitles, type SearchMode } from '@/lib/search'
import { useGraphStore } from '@/state/graphStore'

const MODES: Array<[SearchMode, string, string]> = [
  ['title', 'Title', 'instant fuzzy match over titles, in the browser'],
  ['fulltext', 'Full text', 'exact phrases anywhere in the paper'],
  ['semantic', 'Meaning', 'papers about the same thing, in other words'],
]

// Long enough that a burst of typing is one request, short enough to feel live.
const DEBOUNCE_MS = 260

export function SearchBox() {
  const query = useGraphStore((s) => s.query)
  const setQuery = useGraphStore((s) => s.setQuery)
  const mode = useGraphStore((s) => s.searchMode)
  const setMode = useGraphStore((s) => s.setSearchMode)
  const nodes = useGraphStore((s) => s.nodes)
  const runId = useGraphStore((s) => s.runId)
  const setResults = useGraphStore((s) => s.setSearchResults)
  const setPending = useGraphStore((s) => s.setSearchPending)
  const setError = useGraphStore((s) => s.setSearchError)
  const pending = useGraphStore((s) => s.searchPending)
  const error = useGraphStore((s) => s.searchError)

  const inFlight = useRef<AbortController | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (runId !== null && nodes.length) buildTitleIndex(nodes, runId)
  }, [nodes, runId])

  const run = useCallback(
    (text: string, searchMode: SearchMode) => {
      inFlight.current?.abort()
      const trimmed = text.trim()

      if (trimmed.length < 2) {
        setResults(null)
        setPending(false)
        return
      }

      if (searchMode === 'title') {
        // Local and synchronous — no request to cancel, no spinner to show.
        setResults(new Set(searchTitles(trimmed)))
        setPending(false)
        return
      }

      const controller = new AbortController()
      inFlight.current = controller
      setPending(true)
      searchServer(searchMode, trimmed, controller.signal)
        .then((hits) => {
          if (controller.signal.aborted) return
          setResults(new Set(hits.map((h) => h.paper_id)))
          setPending(false)
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return
          setError(e instanceof Error ? e.message : String(e))
          setPending(false)
        })
    },
    [setResults, setPending, setError],
  )

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    // Title search is local, so it runs on the keystroke; the server modes wait
    // for a pause rather than firing a request per character.
    const delay = mode === 'title' ? 0 : DEBOUNCE_MS
    timer.current = setTimeout(() => run(query, mode), delay)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [query, mode, run])

  useEffect(() => () => inFlight.current?.abort(), [])

  return (
    <div className="search-box">
      <div className="search-input">
        <input
          type="search"
          placeholder={
            mode === 'semantic' ? 'Describe what you are looking for…' : 'Search…'
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {pending && <span className="spinner" aria-label="searching" />}
      </div>
      <div className="search-modes">
        {MODES.map(([value, label, hint]) => (
          <button
            key={value}
            className={mode === value ? 'active' : ''}
            onClick={() => setMode(value)}
            title={hint}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <span className="search-error">{error}</span>}
    </div>
  )
}
