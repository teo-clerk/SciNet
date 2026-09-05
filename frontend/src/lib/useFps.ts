/**
 * Frame-rate probe for the M0 performance gate.
 *
 * Samples outside React's render cycle and reports at 500 ms intervals so the
 * meter itself cannot be what costs the frames.
 */
import { useEffect, useState } from 'react'

export interface FpsSample {
  fps: number
  worstFrameMs: number
}

/** One frame at 60 fps. A window whose worst frame blew through it dropped one. */
export const FRAME_BUDGET_MS = 16.7

export function useFps(): FpsSample {
  const [sample, setSample] = useState<FpsSample>({ fps: 0, worstFrameMs: 0 })

  useEffect(() => {
    let raf = 0
    let frames = 0
    let worst = 0
    let last = performance.now()
    let windowStart = last

    const tick = (now: number) => {
      const delta = now - last
      last = now
      frames++
      if (delta > worst) worst = delta

      if (now - windowStart >= 500) {
        setSample({
          fps: Math.round((frames * 1000) / (now - windowStart)),
          worstFrameMs: Math.round(worst * 10) / 10,
        })
        frames = 0
        worst = 0
        windowStart = now
      }
      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  return sample
}
