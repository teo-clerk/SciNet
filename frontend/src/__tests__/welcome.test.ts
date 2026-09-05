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
  needsMoreForRegions,
  nextIdlePolls,
  splitCode,
  STALL_POLLS,
  welcomeCopy,
  type WelcomeInput,
} from '../lib/welcome'

describe('nextIdlePolls', () => {
  test('counts up while the queue sits untouched', () => {
    expect(nextIdlePolls(0, 5, 0)).toBe(1)
    expect(nextIdlePolls(1, 5, 0)).toBe(2)
  })

  test('resets the moment something runs, or the queue empties', () => {
    expect(nextIdlePolls(3, 5, 1)).toBe(0)
    expect(nextIdlePolls(3, 0, 0)).toBe(0)
  })
})

describe('welcomeCopy', () => {
  test('each state has its own heading', () => {
    expect(welcomeCopy({ kind: 'api-down', progress: null }, 0).heading).toBe(
      'SciNet is not running yet',
    )
    expect(welcomeCopy({ kind: 'empty', progress: null }, 0).heading).toBe(
      'A map of what you read',
    )
    expect(welcomeCopy({ kind: 'building', progress: null }, 0).heading).toBe(
      'Reading your library…',
    )
  })

  test('a build with a tally says how far it is', () => {
    const copy = welcomeCopy({ kind: 'building', progress: { done: 9, total: 40 } }, 0)
    expect(copy.body).toStartWith('9 of 40 works read.')
  })

  test('a build with no tally does not invent one', () => {
    expect(welcomeCopy({ kind: 'building', progress: null }, 0).body).toBe(
      'The map is being built.',
    )
  })

  test('the stalled heading counts the queue, and knows one from many', () => {
    expect(welcomeCopy({ kind: 'worker-down', progress: null }, 12).heading).toBe(
      '12 works are waiting',
    )
    expect(welcomeCopy({ kind: 'worker-down', progress: null }, 1).heading).toBe(
      '1 work is waiting',
    )
  })

  test('the commands are marked as code', () => {
    expect(welcomeCopy({ kind: 'api-down', progress: null }, 0).body).toContain(
      '`uv run scinet-up`',
    )
  })
})

describe('splitCode', () => {
  test('odd segments are code', () => {
    expect(splitCode('run `uv run x` in `backend`.')).toEqual([
      { code: false, text: 'run ' },
      { code: true, text: 'uv run x' },
      { code: false, text: ' in ' },
      { code: true, text: 'backend' },
      { code: false, text: '.' },
    ])
  })

  test('plain prose is one segment', () => {
    expect(splitCode('nothing to see')).toEqual([{ code: false, text: 'nothing to see' }])
  })

  test('a leading code span does not produce an empty prose segment', () => {
    expect(splitCode('`x` first')).toEqual([
      { code: true, text: 'x' },
      { code: false, text: ' first' },
    ])
  })
})

describe('needsMoreForRegions', () => {
  test('no regions and a small library is the case the banner is for', () => {
    expect(needsMoreForRegions(0, 12, 30)).toBe(true)
  })

  test('regions exist: nothing to say', () => {
    expect(needsMoreForRegions(3, 12, 30)).toBe(false)
  })

  test('a library past the floor with no regions is not a size problem', () => {
    expect(needsMoreForRegions(0, 30, 30)).toBe(false)
  })

  test('an unknown floor says nothing', () => {
    expect(needsMoreForRegions(0, 12, null)).toBe(false)
  })
})

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
