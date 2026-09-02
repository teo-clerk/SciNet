/**
 * Filter semantics.
 *
 * Two choices here are deliberate and easy to reverse by accident: tags are
 * OR-ed (AND-ing distinct topics yields an empty map almost every time), and
 * "no filter" returns null so the renderer can skip per-node work entirely on
 * the common path.
 */
import { describe, expect, test } from 'bun:test'

import type { GraphNode } from '../api/graph'

/** Mirrors useVisibleSet, minus the React binding. */
function visibleSet(
  nodes: GraphNode[],
  query: string,
  activeTags: Set<number>,
  yearCutoff: number | null = null,
  quantityResults: Set<number> | null = null,
): Set<number> | null {
  const trimmed = query.trim().toLowerCase()
  if (
    !trimmed &&
    activeTags.size === 0 &&
    yearCutoff === null &&
    quantityResults === null
  ) {
    return null
  }

  const visible = new Set<number>()
  nodes.forEach((node, i) => {
    if (trimmed && !(node.title ?? '').toLowerCase().includes(trimmed)) return
    if (activeTags.size > 0 && !node.tags.some((t) => activeTags.has(t))) return
    if (yearCutoff !== null && node.year !== null && node.year > yearCutoff) return
    if (quantityResults !== null && !quantityResults.has(node.id)) return
    visible.add(i)
  })
  return visible
}

const NODES: GraphNode[] = [
  { id: 1, title: 'Exoplanet Transit Spectroscopy', year: 2020, cluster: 0, tags: [0], pages: 12, confidence: 0.9, provisional: false, drift: 0 },
  { id: 2, title: 'Galaxy Formation Simulations', year: 2021, cluster: 0, tags: [0, 1], pages: 8, confidence: 0.4, provisional: false, drift: 0 },
  { id: 3, title: 'Robotic Grasp Planning', year: 2022, cluster: 1, tags: [1], pages: 30, confidence: 0.7, provisional: true, drift: 2 },
  { id: 4, title: null, year: null, cluster: null, tags: [], pages: null, confidence: null, provisional: false, drift: 0 },
]

describe('visibleSet', () => {
  test('no filter returns null so the renderer can skip the work', () => {
    expect(visibleSet(NODES, '', new Set())).toBeNull()
    expect(visibleSet(NODES, '   ', new Set())).toBeNull()
  })

  test('search matches titles case-insensitively', () => {
    expect(visibleSet(NODES, 'exoplanet', new Set())).toEqual(new Set([0]))
    expect(visibleSet(NODES, 'EXOPLANET', new Set())).toEqual(new Set([0]))
  })

  test('search matches a substring anywhere in the title', () => {
    expect(visibleSet(NODES, 'formation', new Set())).toEqual(new Set([1]))
  })

  test('a node without a title never matches a search', () => {
    expect(visibleSet(NODES, 'a', new Set())?.has(3)).toBe(false)
  })

  test('tags are OR-ed, not AND-ed', () => {
    // AND-ing two distinct topics returns nothing almost every time, which
    // makes the filter feel broken.
    expect(visibleSet(NODES, '', new Set([0, 1]))).toEqual(new Set([0, 1, 2]))
  })

  test('a single tag narrows correctly', () => {
    expect(visibleSet(NODES, '', new Set([1]))).toEqual(new Set([1, 2]))
  })

  test('search and tags intersect', () => {
    expect(visibleSet(NODES, 'galaxy', new Set([1]))).toEqual(new Set([1]))
    expect(visibleSet(NODES, 'exoplanet', new Set([1]))).toEqual(new Set())
  })

  test('an untagged node is excluded by any tag filter', () => {
    expect(visibleSet(NODES, '', new Set([0]))?.has(3)).toBe(false)
  })

  test('a filter matching nothing yields an empty set, not null', () => {
    // null means "no filter active"; conflating the two would silently show
    // the whole corpus when the user expected none of it.
    const result = visibleSet(NODES, 'zzzznomatch', new Set())
    expect(result).not.toBeNull()
    expect(result?.size).toBe(0)
  })

  test('the year cutoff hides what came after', () => {
    expect(visibleSet(NODES, '', new Set(), 2020)).toEqual(new Set([0, 3]))
    expect(visibleSet(NODES, '', new Set(), 2021)).toEqual(new Set([0, 1, 3]))
  })

  test('an undated paper stays visible at every scrubber position', () => {
    // An unknown year is not a "later" year — hiding undated papers would
    // make them flicker into existence only when the filter releases.
    expect(visibleSet(NODES, '', new Set(), 2019)).toEqual(new Set([3]))
  })

  test('a null cutoff is no filter at all', () => {
    expect(visibleSet(NODES, '', new Set(), null)).toBeNull()
  })

  test('the cutoff ANDs with tags — dimensions narrow each other', () => {
    expect(visibleSet(NODES, '', new Set([1]), 2021)).toEqual(new Set([1]))
  })

  test('quantity results filter by paper id, ANDed like everything', () => {
    // Ids, not indices: the server speaks paper ids, the renderer indices.
    expect(visibleSet(NODES, '', new Set(), null, new Set([1, 3]))).toEqual(
      new Set([0, 2]),
    )
    expect(
      visibleSet(NODES, '', new Set([1]), null, new Set([1, 3])),
    ).toEqual(new Set([2]))
  })

  test('an empty quantity match is an empty map, not no filter', () => {
    const result = visibleSet(NODES, '', new Set(), null, new Set())
    expect(result).not.toBeNull()
    expect(result?.size).toBe(0)
  })
})
