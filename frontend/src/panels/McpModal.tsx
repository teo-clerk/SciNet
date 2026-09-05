/**
 * The AI-tools dialog: the library, handed to Claude Code, Claude Desktop,
 * Cursor or Zed.
 *
 * The MCP server is stdio — the client spawns it — so there is no socket to
 * show a status for. What the dialog shows is the command, with this
 * checkout's path filled in by the API, and the tools the server reports
 * about itself. It is the first real dialog in the app: it takes focus while
 * open, closes on Escape and on the backdrop, and hands focus back to the
 * button that opened it.
 */
import { useEffect, useRef, useState } from 'react'

import { fetchMcpRecipe, type McpRecipe } from '@/api/mcp'
import {
  EXAMPLE_PROMPTS,
  MCP_CLIENTS,
  firstSentence,
  snippetFor,
  type McpClient,
} from '@/lib/mcpConfig'
import { useCopy } from '@/lib/useCopy'
import { useGraphStore } from '@/state/graphStore'

const DOCS_URL = 'https://github.com/teo-clerk/SciNet/blob/main/docs/MCP.md'
const FOCUSABLE = 'button, a[href], [tabindex]:not([tabindex="-1"])'

export function McpModal() {
  const open = useGraphStore((s) => s.mcpOpen)
  if (!open) return null
  return <McpModalBody />
}

function McpModalBody() {
  const setMcpOpen = useGraphStore((s) => s.setMcpOpen)
  const [recipe, setRecipe] = useState<McpRecipe | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [client, setClient] = useState<McpClient>('claude-code')
  const { copied, copy } = useCopy()
  const dialog = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    fetchMcpRecipe()
      .then((r) => alive && setRecipe(r))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      alive = false
    }
  }, [])

  // Focus: into the dialog on open, trapped while open, back to the opener
  // on close. The body unmounts when the store flips, so the cleanup is the
  // close path for every way of closing.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialog.current?.focus()

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setMcpOpen(false)
        return
      }
      if (event.key !== 'Tab' || !dialog.current) return
      const items = Array.from(dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE))
      const first = items[0]
      const last = items.at(-1)
      if (!first || !last) return
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === dialog.current)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus()
    }
  }, [setMcpOpen])

  const close = () => setMcpOpen(false)
  const snippet = recipe ? snippetFor(client, recipe) : null

  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) close()
      }}
    >
      <div
        className="mcp-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mcp-title"
        tabIndex={-1}
        ref={dialog}
      >
        <header>
          <h2 id="mcp-title">Your library, as context for your AI tools</h2>
          <button className="close" aria-label="Close" onClick={close}>
            ×
          </button>
        </header>
        <p className="prose lede">
          Claude Code, Claude Desktop, Cursor and Zed can search this library, read its
          works and walk its regions — on this machine, read-only, over the Model Context
          Protocol.
        </p>

        {error && <p className="warn">api unreachable — {error}</p>}
        {!recipe && !error && <p className="dim">Reading the recipe…</p>}

        {recipe && (
          <>
            <div className="facts">
              <span className="chip">stdio</span>
              <span className="chip">{recipe.tools.length} tools</span>
              <span className="chip">read-only</span>
              <span className="chip ok">nothing leaves this machine</span>
              <span className="dim">API {recipe.api_url}</span>
            </div>
            {!recipe.available && (
              <p className="warn">
                the mcp package is not installed — run <code>uv sync</code> in backend/
              </p>
            )}

            <div className="tabs" role="tablist" aria-label="MCP client">
              {MCP_CLIENTS.map((c) => (
                <button
                  key={c.id}
                  role="tab"
                  id={`mcp-tab-${c.id}`}
                  aria-selected={client === c.id}
                  aria-controls="mcp-panel"
                  onClick={() => setClient(c.id)}
                >
                  {c.label}
                </button>
              ))}
            </div>
            {snippet && (
              <div
                className="snippet"
                role="tabpanel"
                id="mcp-panel"
                aria-labelledby={`mcp-tab-${client}`}
              >
                <pre>
                  <code>{snippet.text}</code>
                </pre>
                <button className="copy" onClick={() => void copy(snippet.text)}>
                  {copied ? 'Copied' : 'Copy'}
                </button>
                <p className="dim hint">{snippet.fileHint}</p>
              </div>
            )}

            <h3>What they can do</h3>
            <ul className="tools">
              {recipe.tools.map((tool) => (
                <li key={tool.name}>
                  <code>{tool.name}</code> {firstSentence(tool.description)}
                </li>
              ))}
            </ul>

            <h3>Try asking</h3>
            <ul className="prompts">
              {EXAMPLE_PROMPTS.map((prompt) => (
                <li key={prompt}>“{prompt}”</li>
              ))}
            </ul>
          </>
        )}

        <footer className="dim">
          The API must be running — <code>uv run scinet-up</code> from backend/; when it is
          not, the server tells the assistant so. Full guide:{' '}
          <a href={DOCS_URL} target="_blank" rel="noreferrer">
            docs/MCP.md
          </a>
          .
        </footer>
      </div>
    </div>
  )
}
