/**
 * Adding papers from the interface.
 *
 * Uploads are sent in batches rather than as one request: a few thousand files
 * is several gigabytes, and a single multipart body that size is a timeout
 * waiting to happen and gives no progress until it finishes. Batching means
 * the count advances steadily and a network hiccup costs one batch, not the
 * whole import.
 */
import { useCallback, useRef, useState } from 'react'

/** Everything the backend will read. Kept in step with SUPPORTED_EXTENSIONS in
 *  backend/app/core/paths.py — the upload button and the watched folder have to
 *  agree about what a paper is, or dropping a book in one place works and the
 *  other silently ignores it. */
export const ACCEPTED = [
  '.pdf',
  '.epub',
  '.mobi',
  '.azw3',
  '.djvu',
  '.djv',
  '.docx',
  '.txt',
  '.md',
]

/** A real file extension: a few letters, no digits. Anything else is part of
 *  the name — an arXiv download is called `2504.15673`, and reading `.15673`
 *  as its type is how those got dropped from a library that wanted them. */
const EXTENSION = /\.[a-z]{1,5}$/

/** Permissive by design, and it has to be: the server decides by reading the
 *  bytes, so the only job here is to spare the user from uploading a folder of
 *  images. Anything without a recognisable extension is passed along for the
 *  server to judge. */
export const isReadable = (file: { name: string }): boolean => {
  const name = file.name.toLowerCase()
  const base = name.slice(
    Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\')) + 1,
  )
  const match = base.match(EXTENSION)
  return match === null || ACCEPTED.includes(match[0])
}

const BATCH_SIZE = 20

interface Progress {
  sent: number
  total: number
  queued: number
  duplicates: number
  rejected: string[]
}

async function uploadBatch(batch: File[]): Promise<{
  queued: number
  duplicates: number
  rejected: string[]
}> {
  const form = new FormData()
  for (const file of batch) form.append('files', file)

  const res = await fetch('/api/papers/upload', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`upload failed (${res.status})`)

  const body = (await res.json()) as {
    accepted: Array<{ outcome: string }>
    rejected: Array<{ filename: string; reason: string }>
    queued: number
  }
  return {
    queued: body.queued,
    duplicates: body.accepted.filter((a) => a.outcome !== 'created').length,
    rejected: body.rejected.map((r) => `${r.filename}: ${r.reason}`),
  }
}

export function UploadButton() {
  const input = useRef<HTMLInputElement>(null)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [error, setError] = useState<string | null>(null)

  const send = useCallback(async (files: File[]) => {
    if (files.length === 0) return
    setError(null)
    setProgress({ sent: 0, total: files.length, queued: 0, duplicates: 0, rejected: [] })

    let queued = 0
    let duplicates = 0
    const rejected: string[] = []

    for (let i = 0; i < files.length; i += BATCH_SIZE) {
      const batch = files.slice(i, i + BATCH_SIZE)
      try {
        const result = await uploadBatch(batch)
        queued += result.queued
        duplicates += result.duplicates
        rejected.push(...result.rejected)
      } catch (e) {
        // One failed batch should not discard the ones that succeeded.
        rejected.push(
          ...batch.map((f) => `${f.name}: ${e instanceof Error ? e.message : 'failed'}`),
        )
      }
      setProgress({
        sent: Math.min(i + BATCH_SIZE, files.length),
        total: files.length,
        queued,
        duplicates,
        rejected: [...rejected],
      })
    }

    // Leave the summary up briefly; the drawer takes over from here as the
    // worker starts on them.
    setTimeout(() => setProgress(null), 6000)
  }, [])

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files ?? []).filter(isReadable)
    void send(chosen)
    event.target.value = '' // allow re-picking the same files
  }

  const busy = progress !== null && progress.sent < progress.total

  return (
    <div className="upload">
      <input
        ref={input}
        type="file"
        accept={ACCEPTED.join(',')}
        multiple
        onChange={onPick}
        hidden
      />
      <button onClick={() => input.current?.click()} disabled={busy}>
        {busy ? `Uploading ${progress.sent}/${progress.total}…` : 'Upload papers'}
      </button>

      {progress && !busy && (
        <span className="upload-summary">
          {progress.queued} queued
          {progress.duplicates > 0 && ` · ${progress.duplicates} already present`}
          {progress.rejected.length > 0 && (
            <span
              className="warn"
              title={progress.rejected.slice(0, 20).join('\n')}
            >
              {' '}
              · {progress.rejected.length} rejected
            </span>
          )}
        </span>
      )}
      {error && <span className="warn">{error}</span>}
    </div>
  )
}
