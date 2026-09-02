/**
 * The librarian's transport: a streaming POST, parsed by hand.
 *
 * EventSource cannot send a body, so this is the repo's first fetch-streamed
 * SSE reader. The frame parser is a pure function over text chunks —
 * bun-testable with no fetch anywhere — and the reader loop around it is as
 * small as the AbortController idiom allows. Heartbeat comments are eaten
 * here; handlers only ever see real frames.
 */

export interface MapDirective {
  type: 'fly_to' | 'highlight' | 'trail'
  paper_id?: number
  paper_ids?: number[]
}

export interface DoneInfo {
  cited: number[]
  dropped: number[]
  evidence?: number[]
  error?: string
}

export type LibrarianFrame =
  | { kind: 'status'; text: string }
  | { kind: 'tool_call'; action: string; query: string | null }
  | { kind: 'map_directive'; directive: MapDirective }
  | { kind: 'answer_token'; text: string }
  | { kind: 'done'; info: DoneInfo }

/** Feed raw text chunks; get every frame that has fully arrived. */
export function createFrameParser() {
  let buffer = ''
  return {
    push(chunk: string): LibrarianFrame[] {
      buffer += chunk
      const frames: LibrarianFrame[] = []
      let cut: number
      while ((cut = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, cut)
        buffer = buffer.slice(cut + 2)
        let kind: string | null = null
        let data: string | null = null
        for (const line of block.split('\n')) {
          if (line.startsWith('event: ')) kind = line.slice(7)
          else if (line.startsWith('data: ')) data = line.slice(6)
          // comment lines (": heartbeat") fall through and are ignored
        }
        if (!kind || data === null) continue
        try {
          const payload = JSON.parse(data)
          if (kind === 'status') frames.push({ kind, text: payload.text ?? '' })
          else if (kind === 'tool_call')
            frames.push({ kind, action: payload.action, query: payload.query ?? null })
          else if (kind === 'map_directive')
            frames.push({ kind, directive: payload as MapDirective })
          else if (kind === 'answer_token')
            frames.push({ kind, text: payload.text ?? '' })
          else if (kind === 'done') frames.push({ kind, info: payload as DoneInfo })
        } catch {
          // A malformed frame is not worth breaking the answer over.
        }
      }
      return frames
    },
  }
}

export class LibrarianBusy extends Error {}

export async function askLibrarian(
  question: string,
  onFrame: (frame: LibrarianFrame) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/librarian/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
    signal,
  })
  if (res.status === 409) throw new LibrarianBusy('the librarian is already answering')
  if (!res.ok || !res.body) throw new Error(`librarian -> ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const parser = createFrameParser()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
      onFrame(frame)
    }
  }
}
