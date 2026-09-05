/**
 * Per-browser preferences.
 *
 * "Has this reader turned the lab on" is a fact about the person at the
 * keyboard, not about the library, so it has no business in the database the
 * worker owns. localStorage is the right home — but the accessor itself can
 * throw (a private window with site data blocked, a headless recorder, bun),
 * and a preference that crashes the app on read is worse than no preference.
 * Everything here degrades to the fallback rather than to an error.
 */

export const LAB_KEY = 'scinet.lab'

/** The two calls a preference needs; a real Storage satisfies it, so does a Map. */
export type PrefStore = Pick<Storage, 'getItem' | 'setItem'>

export function safeStorage(): Storage | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

export function readPref(
  key: string,
  fallback: string,
  storage: PrefStore | null = safeStorage(),
): string {
  if (!storage) return fallback
  try {
    return storage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

export function writePref(
  key: string,
  value: string,
  storage: PrefStore | null = safeStorage(),
): void {
  if (!storage) return
  try {
    storage.setItem(key, value)
  } catch {
    // Quota, private mode, a locked-down browser: the preference is a
    // convenience, and losing it is not worth an error the reader cannot act on.
  }
}

/**
 * Whether the lab is on.
 *
 * A `?lab=1` / `?lab=0` query parameter beats the stored preference, so a demo
 * recording or a bug report can force a state without first clearing someone's
 * localStorage — and the same URL shows the same screen on two machines.
 */
export function readLabPref(
  storage: PrefStore | null = safeStorage(),
  search: string = typeof location !== 'undefined' ? location.search : '',
): boolean {
  const forced = new URLSearchParams(search).get('lab')
  if (forced === '1') return true
  if (forced === '0') return false
  return readPref(LAB_KEY, '0', storage) === '1'
}
