/**
 * What to say when there is no map to show.
 *
 * A blank canvas has four different causes and the reader can act on each one
 * differently: the API is not running, the library is empty, the worker is
 * still building the first projection, or jobs are queued and nothing is
 * taking them. The first two are one poll's worth of information. The last two
 * look identical in a single sample — queued > 0 either way — and are told
 * apart only by watching: a queue that has not moved for two polls with
 * nothing running has no worker behind it.
 */

export type WelcomeKind = 'api-down' | 'empty' | 'building' | 'worker-down'

export interface WelcomeInput {
  apiReachable: boolean
  paperCount: number | null
  queued: number | null
  running: number | null
  parseDone: number | null
  /** Consecutive polls that saw jobs queued and none running. */
  idleQueuePolls: number
}

export interface WelcomeState {
  kind: WelcomeKind
  progress: { done: number; total: number } | null
}

/** Polls a queue may sit untouched before it is called stalled. One is too
 *  few — the worker is between jobs for longer than a poll interval whenever it
 *  frees the GPU. Two stalled polls is eight seconds of nothing. */
export const STALL_POLLS = 2

/** Parse jobs are one per paper, which makes them the honest denominator for
 *  "X of Y" — the same rule the jobs drawer uses. */
export function countParseDone(counts: Record<string, number>): number {
  return Object.entries(counts)
    .filter(([key]) => /^parse:.*:done$/.test(key))
    .reduce((n, [, v]) => n + v, 0)
}

export function deriveWelcome(input: WelcomeInput): WelcomeState {
  if (!input.apiReachable) return { kind: 'api-down', progress: null }

  const queued = input.queued ?? 0
  const running = input.running ?? 0
  const outstanding = queued + running

  if (input.paperCount === 0) return { kind: 'empty', progress: null }
  if (input.paperCount === null && outstanding === 0) {
    return { kind: 'empty', progress: null }
  }

  if (outstanding > 0) {
    if (running > 0 || input.idleQueuePolls < STALL_POLLS) {
      return {
        kind: 'building',
        progress: { done: input.parseDone ?? 0, total: input.paperCount ?? 0 },
      }
    }
    return { kind: 'worker-down', progress: null }
  }

  // Papers exist and nothing is queued, yet the caller has no map: the
  // projection is about to land, or a refit is wanted. Either way the honest
  // word is "building" and there is no meaningful fraction to put beside it.
  return { kind: 'building', progress: null }
}
