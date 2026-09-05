/**
 * Adding works from the interface.
 *
 * The batching and the tally live in lib/upload; this is the picker, the
 * button, and the six seconds the summary stays on screen before the jobs
 * drawer takes over as the worker starts on them.
 */
import { useCallback, useRef, useState } from 'react'

import { ACCEPTED, isReadable, uploadFiles, type Progress } from '@/lib/upload'

// Re-exported so the accepted-format tests keep one import path: the button
// and the watched folder have to agree about what a work is.
export { ACCEPTED, isReadable } from '@/lib/upload'

const SUMMARY_MS = 6000

export function UploadButton() {
  const input = useRef<HTMLInputElement>(null)
  const [progress, setProgress] = useState<Progress | null>(null)

  const send = useCallback(async (files: File[]) => {
    if (files.length === 0) return
    await uploadFiles(files, setProgress)
    // Leave the summary up briefly; the drawer takes over from here.
    setTimeout(() => setProgress(null), SUMMARY_MS)
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
        {busy ? `Uploading ${progress.sent}/${progress.total}…` : 'Add works'}
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
    </div>
  )
}
