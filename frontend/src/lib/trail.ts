/**
 * The arithmetic behind an idea trail.
 *
 * The server thinks in paper ids and the renderer in node indices, so every
 * trail crosses that boundary once, here. The rest is the small set of rules
 * the panel and the markers share — what a stop is called, which hop is the
 * leap, where the tour goes next — kept apart from React so a test can call
 * them without a canvas.
 */
import type { GraphCluster, GraphNode } from '@/api/graph'

/** One stop on a trail, in the shape the server sends and the panel keeps. */
export interface TrailStop {
  paper_id: number
  title: string | null
  year: number | null
  cluster_id: number | null
  cluster_label: string | null
  /** Cosine to the following stop; null on the last one. */
  similarity_to_next: number | null
  /** The work's central question in plain words, when INSIGHT has written it. */
  core_question: string | null
}

/** One end of a trail: a paper the reader chose, or a phrase they typed. */
export interface TrailEnd {
  paperId: number | null
  text: string
}

export type TrailMode = 'path' | 'reading-order'

/** Shortest phrase the server will anchor; matches its `min_length`. */
export const MIN_PHRASE = 2

/** Seconds between tour steps: long enough to read a title and a question. */
export const TOUR_STEP_MS = 2600

/** More markers than this and the numbers pile up faster than they help. */
export const MAX_MARKERS = 14

/** Node indices for the stops that are on the map, in trail order. A stop the
 *  map does not know — a paper removed since the path was computed — is
 *  dropped rather than drawn at index -1. */
export function stopsToIndices(
  stops: ReadonlyArray<{ paper_id: number }>,
  nodes: ReadonlyArray<{ id: number }>,
): number[] {
  const byId = new Map(nodes.map((node, i) => [node.id, i] as const))
  return stops
    .map((stop) => byId.get(stop.paper_id))
    .filter((i): i is number => i !== undefined)
}

/** `3 · 1911 · Stoic Ethics` — the position always, the rest when known. */
export function stopCaption(
  stop: Pick<TrailStop, 'year' | 'cluster_label'>,
  position: number,
): string {
  return [String(position), stop.year === null ? null : String(stop.year), stop.cluster_label]
    .filter((part): part is string => part !== null && part !== '')
    .join(' · ')
}

/** Where the tour goes after `cursor`; null once it has shown the last stop. */
export function tourStep(cursor: number | null, count: number): number | null {
  if (count <= 0) return null
  const next = cursor === null ? 0 : cursor + 1
  return next < count ? next : null
}

/** Only the final hop of an incomplete path is a leap — the server walked as
 *  far as the kNN graph reaches and then jumped to the destination. */
export function hopIsJump(index: number, count: number, complete: boolean): boolean {
  return !complete && count >= 2 && index === count - 1
}

/** Stops built on the client, for a reading order the map already knows
 *  everything about. Unknown ids are dropped, as in `stopsToIndices`. */
export function stopsFromNodes(
  paperIds: readonly number[],
  nodes: readonly GraphNode[],
  clusters: readonly GraphCluster[],
): TrailStop[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node] as const))
  const labelById = new Map(clusters.map((c) => [c.id, c.label] as const))
  return paperIds.flatMap((id) => {
    const node = nodeById.get(id)
    if (!node) return []
    return [
      {
        paper_id: id,
        title: node.title,
        year: node.year,
        cluster_id: node.cluster,
        cluster_label: node.cluster === null ? null : (labelById.get(node.cluster) ?? null),
        similarity_to_next: null,
        core_question: null,
      },
    ]
  })
}

/** An end is usable when it names a paper or carries a phrase long enough to anchor. */
export function endIsSet(end: TrailEnd): boolean {
  return end.paperId !== null || end.text.trim().length >= MIN_PHRASE
}

/** The query string for `/api/graph/path`: an id wins over a phrase at each end. */
export function pathQuery(from: TrailEnd, to: TrailEnd): string {
  const params = new URLSearchParams()
  if (from.paperId !== null) params.set('from', String(from.paperId))
  else params.set('from_text', from.text.trim())
  if (to.paperId !== null) params.set('to', String(to.paperId))
  else params.set('to_text', to.text.trim())
  return params.toString()
}
