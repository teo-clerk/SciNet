/**
 * Adding works from the interface.
 *
 * The batching and the tally live in lib/upload, the React state around them
 * in lib/useUploader — shared with the drop zones, so a file dropped on the
 * map and one chosen here are counted the same way. This is the picker and
 * the button.
 */
import { useRef } from 'react'

import { ACCEPTED, isReadable } from '@/lib/upload'
import { useUploader } from '@/lib/useUploader'
import { UploadSummary } from '@/panels/UploadSummary'

// Re-exported so the accepted-format tests keep one import path: the button
// and the watched folder have to agree about what a work is.
export { ACCEPTED, isReadable } from '@/lib/upload'

export function UploadButton() {
  const input = useRef<HTMLInputElement>(null)
  const { progress, busy, send } = useUploader()

  const onPick = (event: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files ?? []).filter(isReadable)
    void send(chosen)
    event.target.value = '' // allow re-picking the same files
  }

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
        {busy && progress ? `Uploading ${progress.sent}/${progress.total}…` : 'Add works'}
      </button>

      {/* The button's own label carries the count while batches go out. */}
      {progress && !busy && <UploadSummary progress={progress} busy={false} />}
    </div>
  )
}
