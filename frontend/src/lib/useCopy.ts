/**
 * Copy text to the clipboard and say so, briefly.
 *
 * The clipboard needs a secure context and a user gesture; a bare file://
 * page or an old browser has neither, and whatever is being copied is still
 * readable on screen, so a refusal is silent. "Copied" reverts after a
 * moment: the same button is pressed again for the next snippet, and a label
 * that never changes back cannot confirm the second press.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export const COPIED_RESET_MS = 1600

export interface CopyState {
  copied: boolean
  copy: (text: string) => Promise<void>
}

export function useCopy(resetMs = COPIED_RESET_MS): CopyState {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  const copy = useCallback(
    async (text: string) => {
      if (typeof navigator === 'undefined' || !navigator.clipboard) return
      try {
        await navigator.clipboard.writeText(text)
        setCopied(true)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), resetMs)
      } catch {
        setCopied(false)
      }
    },
    [resetMs],
  )

  return { copied, copy }
}
