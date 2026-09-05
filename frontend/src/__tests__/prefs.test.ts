/**
 * Preferences survive a hostile browser.
 *
 * The lab toggle is remembered in localStorage, and localStorage is the one
 * browser API that throws on *access* — a private window with site data
 * blocked, or bun, where it does not exist. A preference must never be the
 * reason the app fails to open.
 */
import { describe, expect, test } from 'bun:test'

import {
  LAB_KEY,
  readLabPref,
  readPref,
  safeStorage,
  writePref,
  type PrefStore,
} from '../lib/prefs'

const fakeStorage = (seed: Record<string, string> = {}): PrefStore => {
  const map = new Map(Object.entries(seed))
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => void map.set(key, value),
  }
}

const throwing: PrefStore = {
  getItem: () => {
    throw new DOMException('blocked', 'SecurityError')
  },
  setItem: () => {
    throw new DOMException('quota', 'QuotaExceededError')
  },
}

describe('safeStorage', () => {
  test('answers null rather than throwing where there is no localStorage', () => {
    // bun has none; the browser cases that throw are simulated below.
    expect(safeStorage()).toBeNull()
  })
})

describe('readPref / writePref', () => {
  test('falls back when the store is missing', () => {
    expect(readPref('x', 'fallback', null)).toBe('fallback')
  })

  test('falls back when the store throws', () => {
    expect(readPref('x', 'fallback', throwing)).toBe('fallback')
  })

  test('a write into a throwing store is swallowed', () => {
    expect(() => writePref('x', '1', throwing)).not.toThrow()
  })

  test('write then read round-trips', () => {
    const store = fakeStorage()
    writePref(LAB_KEY, '1', store)
    expect(readPref(LAB_KEY, '0', store)).toBe('1')
  })
})

describe('readLabPref', () => {
  test('defaults to off', () => {
    expect(readLabPref(fakeStorage(), '')).toBe(false)
    expect(readLabPref(null, '')).toBe(false)
  })

  test('reads the stored preference', () => {
    expect(readLabPref(fakeStorage({ [LAB_KEY]: '1' }), '')).toBe(true)
    expect(readLabPref(fakeStorage({ [LAB_KEY]: '0' }), '')).toBe(false)
  })

  test('the URL wins both ways', () => {
    // A demo recording forces the lab on; a bug report forces it off. Neither
    // should have to clear the reader's localStorage first.
    expect(readLabPref(fakeStorage({ [LAB_KEY]: '0' }), '?lab=1')).toBe(true)
    expect(readLabPref(fakeStorage({ [LAB_KEY]: '1' }), '?lab=0')).toBe(false)
  })

  test('an unrecognised URL value defers to the store', () => {
    expect(readLabPref(fakeStorage({ [LAB_KEY]: '1' }), '?lab=maybe')).toBe(true)
  })

  test('a throwing store reads as off', () => {
    expect(readLabPref(throwing, '')).toBe(false)
  })
})
