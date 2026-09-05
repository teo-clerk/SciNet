/**
 * Dropping files on the map.
 *
 * Listens on the document rather than on an element of its own: a full-size
 * element over the canvas would take every pointer event the map needs, and
 * one with `pointer-events: none` never sees the drag begin. So the document
 * says when a file drag is in the window, and the overlay is only a sheet of
 * text that appears for the duration — the drop itself lands wherever it
 * lands, and the document handler catches it.
 */
import { useEffect, useState } from 'react'

import { dragCarriesFiles } from '@/lib/upload'
import { useUploader } from '@/lib/useUploader'
import { UploadSummary } from '@/panels/UploadSummary'

export function DropOverlay() {
  const { progress, busy, send } = useUploader()
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    const over = (event: DragEvent) => {
      if (!dragCarriesFiles(event.dataTransfer?.types)) return
      event.preventDefault() // or the browser opens the file instead
      setDragging(true)
    }
    // relatedTarget is null only when the pointer leaves the window; a leave
    // into another element is not the end of the drag.
    const leave = (event: DragEvent) => {
      if (event.relatedTarget === null) setDragging(false)
    }
    const drop = (event: DragEvent) => {
      if (!dragCarriesFiles(event.dataTransfer?.types)) return
      event.preventDefault()
      setDragging(false)
      void send(Array.from(event.dataTransfer?.files ?? []))
    }
    document.addEventListener('dragover', over)
    document.addEventListener('dragleave', leave)
    document.addEventListener('drop', drop)
    return () => {
      document.removeEventListener('dragover', over)
      document.removeEventListener('dragleave', leave)
      document.removeEventListener('drop', drop)
    }
  }, [send])

  if (!dragging && !progress) return null

  return (
    <div className={dragging ? 'drop-overlay dropping' : 'drop-overlay'} aria-live="polite">
      {dragging ? (
        <span>Drop to add to the library</span>
      ) : (
        progress && <UploadSummary progress={progress} busy={busy} />
      )}
    </div>
  )
}
