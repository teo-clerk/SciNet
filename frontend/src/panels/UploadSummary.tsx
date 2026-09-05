/**
 * One upload's tally, in words.
 *
 * Three places show it — the button, the map overlay, the welcome screen —
 * and a reader who drops a folder in one place and picks files in another
 * should read the same sentence both times.
 */
import type { Progress } from '@/lib/upload'

export function UploadSummary({ progress, busy }: { progress: Progress; busy: boolean }) {
  if (busy) {
    return (
      <span className="upload-summary">
        Uploading {progress.sent} / {progress.total}…
      </span>
    )
  }
  return (
    <span className="upload-summary">
      {progress.queued} queued
      {progress.duplicates > 0 && ` · ${progress.duplicates} already present`}
      {progress.rejected.length > 0 && (
        <span className="warn" title={progress.rejected.slice(0, 20).join('\n')}>
          {' '}
          · {progress.rejected.length} rejected
        </span>
      )}
    </span>
  )
}
