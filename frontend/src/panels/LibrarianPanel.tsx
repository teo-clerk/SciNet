/**
 * The librarian's window: ask, watch it work, read the cited answer.
 *
 * The transcript is component state — only this panel reads it, and a store
 * slice would re-render nothing else anyway (the selector discipline). What
 * goes THROUGH the store is exactly what the map consumes: highlight set,
 * trail, camera requests — converted here from paper ids to node indices,
 * because the renderer thinks in indices and the server in ids.
 *
 * A bottom-centre drawer, deliberately: the left edge belongs to the
 * cluster inspector, the right to the paper card, and an answer about the
 * map wants to sit under the map it is pointing at.
 */
import { useEffect, useRef, useState } from 'react'

import {
  askLibrarian,
  LibrarianBusy,
  type DoneInfo,
  type MapDirective,
} from '@/api/librarian'
import { frameForSet } from '@/lib/framing'
import { useGraphStore } from '@/state/graphStore'

interface Turn {
  role: 'user' | 'librarian'
  text: string
  cited?: number[]
  dropped?: number[]
}

export function LibrarianPanel() {
  const open = useGraphStore((s) => s.librarianOpen)
  const toggle = useGraphStore((s) => s.toggleLibrarian)
  const nodes = useGraphStore((s) => s.nodes)
  const buffers = useGraphStore((s) => s.buffers)
  const setHighlight = useGraphStore((s) => s.setHighlight)
  const setTrail = useGraphStore((s) => s.setTrail)
  const flyToPoint = useGraphStore((s) => s.flyToPoint)
  const setSelected = useGraphStore((s) => s.setSelected)
  const setView = useGraphStore((s) => s.setView)

  const [turns, setTurns] = useState<Turn[]>([])
  const [status, setStatus] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const inFlight = useRef<AbortController | null>(null)
  const logRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => () => inFlight.current?.abort(), [])
  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [turns, status])

  if (!open) return null

  const indexOf = (id: number) => nodes.findIndex((n) => n.id === id)
  const indicesOf = (ids: number[]) =>
    ids.map(indexOf).filter((i): i is number => i >= 0)

  const applyDirective = (directive: MapDirective) => {
    if (directive.type === 'highlight' && directive.paper_ids) {
      setHighlight(new Set(indicesOf(directive.paper_ids)))
    } else if (directive.type === 'trail' && directive.paper_ids) {
      setTrail(indicesOf(directive.paper_ids))
    } else if (directive.type === 'fly_to' && buffers) {
      const ids =
        directive.paper_ids ??
        (directive.paper_id !== undefined ? [directive.paper_id] : [])
      const framing = frameForSet(indicesOf(ids), buffers.positions)
      if (framing) flyToPoint(framing.target, framing.distance)
    }
  }

  const ask = () => {
    const trimmed = question.trim()
    if (!trimmed || asking) return
    setView('map') // the answer moves the map; the reader should see it move
    setTurns((prev) => [...prev, { role: 'user', text: trimmed }])
    setQuestion('')
    setAsking(true)
    setStatus('asking…')
    setHighlight(null)
    setTrail([])

    const controller = new AbortController()
    inFlight.current = controller
    let answer = ''
    const upsertAnswer = (extra: Partial<Turn> = {}) =>
      setTurns((prev) => {
        const next = [...prev]
        const last = next[next.length - 1]
        if (last?.role === 'librarian') {
          next[next.length - 1] = { ...last, text: answer, ...extra }
        } else {
          next.push({ role: 'librarian', text: answer, ...extra })
        }
        return next
      })

    askLibrarian(
      trimmed,
      (frame) => {
        if (frame.kind === 'status') setStatus(frame.text)
        else if (frame.kind === 'tool_call')
          setStatus(`${frame.action}: ${frame.query ?? ''}`)
        else if (frame.kind === 'map_directive') applyDirective(frame.directive)
        else if (frame.kind === 'answer_token') {
          answer += frame.text
          upsertAnswer()
        } else if (frame.kind === 'done') {
          const info: DoneInfo = frame.info
          upsertAnswer({ cited: info.cited, dropped: info.dropped })
          setStatus(info.error ? `failed: ${info.error}` : null)
        }
      },
      controller.signal,
    )
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setStatus(
          error instanceof LibrarianBusy
            ? error.message
            : `could not reach the librarian: ${
                error instanceof Error ? error.message : String(error)
              }`,
        )
      })
      .finally(() => setAsking(false))
  }

  const focusPaper = (id: number) => {
    const index = indexOf(id)
    if (index >= 0) {
      setView('map')
      setSelected(index)
    }
  }

  const titleOf = (id: number) =>
    nodes.find((n) => n.id === id)?.title ?? `#${id}`

  return (
    <aside className="librarian-panel">
      <header>
        <h3>Librarian</h3>
        <button className="close" aria-label="Close" onClick={toggle}>
          ×
        </button>
      </header>
      <div className="log" ref={logRef}>
        {turns.length === 0 && (
          <p className="dim">
            Ask the library a question. The answer cites papers, and the map
            flies to the evidence.
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`turn ${turn.role}`}>
            <p>{turn.text}</p>
            {turn.cited && turn.cited.length > 0 && (
              <div className="citations">
                {turn.cited.map((id) => (
                  <button
                    key={id}
                    className="tag-chip"
                    title="Open on the map"
                    onClick={() => focusPaper(id)}
                  >
                    {titleOf(id)}
                  </button>
                ))}
              </div>
            )}
            {turn.dropped && turn.dropped.length > 0 && (
              <p className="dim small">
                {turn.dropped.length} citation(s) removed — not in the
                evidence the tools returned.
              </p>
            )}
          </div>
        ))}
        {status && <p className="dim status">{status}</p>}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          ask()
        }}
      >
        <input
          value={question}
          placeholder="What does my library say about…"
          onChange={(event) => setQuestion(event.target.value)}
          disabled={asking}
        />
        <button type="submit" disabled={asking || !question.trim()}>
          {asking ? '…' : 'Ask'}
        </button>
      </form>
    </aside>
  )
}
