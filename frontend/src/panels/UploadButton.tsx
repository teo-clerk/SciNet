/**
 * Adding papers from the interface.
 *
 * Uploads are sent in batches rather than as one request: a few thousand PDFs
 * is several gigabytes, and a single multipart body that size is a timeout
 * waiting to happen and gives no progress until it finishes. Batching means
 * the count advances steadily and a network hiccup costs one batch, not the
 * whole import.
 */
import { useCallback, useRef, useState } from 'react'

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
    const chosen = Array.from(event.target.files ?? []).filter((f) =>
      f.name.toLowerCase().endsWith('.pdf'),
    )
    void send(chosen)
    event.target.value = '' // allow re-picking the same files
  }

  const busy = progress !== null && progress.sent < progress.total

  return (
    <div className="upload">
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
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
