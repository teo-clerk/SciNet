/**
 * One upload at a time, with a summary that lingers.
 *
 * Shared by the button in the filter bar, the welcome screen's drop zone and
 * the overlay that catches a drop on the map: three ways in, one tally. The
 * filter, the batching and the arithmetic are in lib/upload; this is only the
 * React state around them and the six seconds the summary stays up before the
 * jobs drawer takes over as the worker starts on the files.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { isReadable, uploadFiles, type Progress } from '@/lib/upload'

const SUMMARY_MS = 6000

export interface Uploader {
  progress: Progress | null
  /** True while batches are still going out. */
  busy: boolean
  /** Filters, sends, and keeps the tally; a list with nothing readable is a no-op. */
  send: (files: File[]) => Promise<void>
}

export function useUploader(): Uploader {
  const [progress, setProgress] = useState<Progress | null>(null)
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (clearTimer.current) clearTimeout(clearTimer.current)
    },
    [],
  )

  const send = useCallback(async (files: File[]) => {
    const readable = files.filter(isReadable)
    if (readable.length === 0) return
    if (clearTimer.current) clearTimeout(clearTimer.current)
    await uploadFiles(readable, setProgress)
    clearTimer.current = setTimeout(() => setProgress(null), SUMMARY_MS)
  }, [])

  const busy = progress !== null && progress.sent < progress.total
  return { progress, busy, send }
}
