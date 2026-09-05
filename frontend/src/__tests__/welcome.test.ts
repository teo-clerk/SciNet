/**
 * Four reasons for a blank canvas, told apart.
 *
 * The one that matters most is the one a single poll cannot see: jobs queued
 * and nothing taking them looks exactly like a worker between two jobs. Only
 * watching the queue not move separates "be patient" from "start the worker".
 */
import { describe, expect, test } from 'bun:test'

import {
  countParseDone,
  deriveWelcome,
  STALL_POLLS,
  type WelcomeInput,
} from '../lib/welcome'

const input = (overrides: Partial<WelcomeInput> = {}): WelcomeInput => ({
  apiReachable: true,
  paperCount: 40,
  queued: 0,
  running: 0,
  parseDone: 0,
  idleQueuePolls: 0,
  ...overrides,
})

describe('deriveWelcome', () => {
  test('an unreachable API comes first, whatever else is known', () => {
    expect(deriveWelcome(input({ apiReachable: false, queued: 5 })).kind).toBe(
      'api-down',
    )
  })

  test('no papers is an empty library', () => {
    expect(deriveWelcome(input({ paperCount: 0 })).kind).toBe('empty')
  })

  test('an unknown count with nothing queued is read as empty', () => {
    expect(deriveWelcome(input({ paperCount: null })).kind).toBe('empty')
  })

  test('an unknown count with work queued is a build, not an empty library', () => {
    const state = deriveWelcome(input({ paperCount: null, queued: 3, running: 1 }))
    expect(state.kind).toBe('building')
    expect(state.progress).toEqual({ done: 0, total: 0 })
  })

  test('a running job is a build in progress, with the parse tally', () => {
    const state = deriveWelcome(
      input({ paperCount: 40, queued: 30, running: 1, parseDone: 9 }),
    )
    expect(state).toEqual({ kind: 'building', progress: { done: 9, total: 40 } })
  })

  test('a queue that has only just been seen idle is still a build', () => {
    // The worker frees the GPU between job kinds, and that gap outlasts a poll.
    const state = deriveWelcome(
      input({ queued: 12, running: 0, idleQueuePolls: STALL_POLLS - 1 }),
    )
    expect(state.kind).toBe('building')
  })

  test('a queue idle for two polls has no worker behind it', () => {
    const state = deriveWelcome(
      input({ queued: 12, running: 0, idleQueuePolls: STALL_POLLS }),
    )
    expect(state).toEqual({ kind: 'worker-down', progress: null })
  })

  test('a running job clears the stall verdict however long the queue sat', () => {
    expect(
      deriveWelcome(input({ queued: 12, running: 1, idleQueuePolls: 10 })).kind,
    ).toBe('building')
  })

  test('papers but no jobs and no map is a build with no fraction', () => {
    expect(deriveWelcome(input())).toEqual({ kind: 'building', progress: null })
  })

  test('null job counts read as zero', () => {
    expect(deriveWelcome(input({ queued: null, running: null })).kind).toBe(
      'building',
    )
  })

  test('the stall rule is two polls', () => {
    expect(STALL_POLLS).toBe(2)
  })
})

describe('countParseDone', () => {
  test('sums only finished parse jobs', () => {
    expect(
      countParseDone({
        'parse:tier0:done': 7,
        'parse:tier2:done': 2,
        'parse:tier0:queued': 30,
        'embed:done': 100,
        'parse:running': 1,
      }),
    ).toBe(9)
  })

  test('is zero for an empty table', () => {
    expect(countParseDone({})).toBe(0)
  })
})
