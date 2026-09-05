/**
 * Whether the reader has asked the operating system for less motion.
 *
 * The CSS side is a media query; this is the same question asked from script,
 * for the animations that are driven by requestAnimationFrame rather than by a
 * stylesheet. Guarded because `matchMedia` is a browser API: bun has none, and
 * an unguarded call at module load would take the tests down with it.
 */

type MediaQuery = (query: string) => { matches: boolean }

const REDUCE = '(prefers-reduced-motion: reduce)'

function globalMatchMedia(): MediaQuery | undefined {
  // Bound, because matchMedia is a Window method: called loose it throws
  // "Illegal invocation" in Chromium.
  return typeof matchMedia === 'function' ? matchMedia.bind(globalThis) : undefined
}

export function prefersReducedMotion(
  match: MediaQuery | undefined = globalMatchMedia(),
): boolean {
  if (!match) return false
  try {
    return match(REDUCE).matches
  } catch {
    return false
  }
}
