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

/** The stall counter after one more poll: up by one while jobs sit queued with
 *  nothing running, back to zero the moment anything moves. */
export function nextIdlePolls(previous: number, queued: number, running: number): number {
  return queued > 0 && running === 0 ? previous + 1 : 0
}

export interface WelcomeCopy {
  heading: string
  /** Prose; `backticks` mark the parts to set as code. */
  body: string
}

/** What the welcome says in each state. `waiting` is the queued job count,
 *  which is the only number the worker-down heading needs. */
export function welcomeCopy(state: WelcomeState, waiting: number): WelcomeCopy {
  switch (state.kind) {
    case 'api-down':
      return {
        heading: 'SciNet is not running yet',
        body:
          "The map's engine is not reachable. From the `backend` folder run " +
          '`uv run scinet-up`, then reload.',
      }
    case 'empty':
      return {
        heading: 'A map of what you read',
        body:
          'Drop papers, books, essays or notes here — PDF, EPUB, DOCX, Markdown ' +
          'or text. Everything stays on this machine.',
      }
    case 'building':
      return {
        heading: 'Reading your library…',
        body: state.progress
          ? `${state.progress.done} of ${state.progress.total} works read. The map ` +
            'appears once enough are placed; regions are named a little after that.'
          : 'The map is being built.',
      }
    case 'worker-down':
      return {
        heading: `${waiting} ${waiting === 1 ? 'work is' : 'works are'} waiting`,
        body:
          'Nothing is processing them. Start the worker from the `backend` folder: ' +
          '`uv run scinet-up`.',
      }
  }
}

/** Prose split at its backticks, so a renderer can set the code parts as code
 *  without a Markdown library for two words. Odd segments are code. */
export function splitCode(text: string): Array<{ code: boolean; text: string }> {
  return text
    .split('`')
    .map((part, i) => ({ code: i % 2 === 1, text: part }))
    .filter((part) => part.text !== '')
}

/** A map with no regions is not broken when the library is simply small; the
 *  clusterer needs a floor of works before it will name anything. Below that
 *  floor the honest message is "add more", not silence. */
export function needsMoreForRegions(
  clusterCount: number,
  paperCount: number,
  minPapers: number | null,
): boolean {
  return minPapers !== null && clusterCount === 0 && paperCount < minPapers
}
