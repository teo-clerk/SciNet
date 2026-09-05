/**
 * An element that accepts dropped files.
 *
 * The browser's drag events are noisier than they look: `dragleave` fires
 * every time the pointer crosses into a child of the zone, so a naive handler
 * flickers the highlight off over every heading. The leave is honoured only
 * when the pointer has actually left the element's subtree.
 */
import { useState, type DragEvent } from 'react'

import { dragCarriesFiles } from '@/lib/upload'

export interface DropZone {
  /** True while a file drag is over the element. */
  dragging: boolean
  dropProps: {
    onDragOver: (event: DragEvent<HTMLElement>) => void
    onDragLeave: (event: DragEvent<HTMLElement>) => void
    onDrop: (event: DragEvent<HTMLElement>) => void
  }
}

export function useDropZone(onFiles: (files: File[]) => void): DropZone {
  const [dragging, setDragging] = useState(false)

  const onDragOver = (event: DragEvent<HTMLElement>) => {
    if (!dragCarriesFiles(event.dataTransfer?.types)) return
    // Without this the browser refuses the drop and opens the file instead.
    event.preventDefault()
    if (!dragging) setDragging(true)
  }

  const onDragLeave = (event: DragEvent<HTMLElement>) => {
    const next = event.relatedTarget as Node | null
    if (next && event.currentTarget.contains(next)) return
    setDragging(false)
  }

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    setDragging(false)
    onFiles(Array.from(event.dataTransfer?.files ?? []))
  }

  return { dragging, dropProps: { onDragOver, onDragLeave, onDrop } }
}
