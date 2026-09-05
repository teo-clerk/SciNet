/**
 * When the filter bar offers to say where to start.
 */
import { describe, expect, test } from 'bun:test'

import { canAskWhereToStart, ENTRY_POINT_MAX, ENTRY_POINT_MIN } from '../lib/entryPoint'

describe('canAskWhereToStart', () => {
  test('one paper is its own starting point', () => {
    expect(canAskWhereToStart(1)).toBe(false)
    expect(canAskWhereToStart(0)).toBe(false)
  })

  test('two is a choice', () => {
    expect(canAskWhereToStart(ENTRY_POINT_MIN)).toBe(true)
  })

  test('the whole library is a different question', () => {
    expect(canAskWhereToStart(ENTRY_POINT_MAX)).toBe(true)
    expect(canAskWhereToStart(ENTRY_POINT_MAX + 1)).toBe(false)
  })
})
