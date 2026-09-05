/**
 * The rules a trail is drawn by, without a canvas.
 *
 * The one that matters most is the id-to-index crossing: the server names
 * papers, the renderer positions nodes, and a paper the map no longer holds
 * must fall out of the trail rather than be drawn at index -1.
 */
import { describe, expect, test } from 'bun:test'

import type { GraphCluster, GraphNode } from '../api/graph'
import {
  endIsSet,
  hopIsJump,
  pathQuery,
  stopCaption,
  stopsFromNodes,
  stopsToIndices,
  tourStep,
} from '../lib/trail'

const node = (id: number, extra: Partial<GraphNode> = {}): GraphNode => ({
  id,
  title: `Paper ${id}`,
  year: 1900 + id,
  cluster: null,
  tags: [],
  pages: null,
  confidence: null,
  provisional: false,
  drift: 1,
  ...extra,
})

const nodes = [node(10), node(20, { cluster: 3 }), node(30, { year: null })]
const clusters: GraphCluster[] = [{ id: 3, label: 'Stoic Ethics', size: 2, terms: [] }]

describe('stopsToIndices', () => {
  test('maps ids to positions in the node array, in trail order', () => {
    expect(stopsToIndices([{ paper_id: 30 }, { paper_id: 10 }], nodes)).toEqual([2, 0])
  })

  test('drops ids the map does not know', () => {
    expect(stopsToIndices([{ paper_id: 10 }, { paper_id: 99 }, { paper_id: 20 }], nodes)).toEqual(
      [0, 1],
    )
  })

  test('an empty trail is an empty list', () => {
    expect(stopsToIndices([], nodes)).toEqual([])
  })
})

describe('stopCaption', () => {
  test('position, year and region, dotted', () => {
    expect(stopCaption({ year: 1911, cluster_label: 'Stoic Ethics' }, 3)).toBe(
      '3 · 1911 · Stoic Ethics',
    )
  })

  test('omits what is missing, keeps the position', () => {
    expect(stopCaption({ year: null, cluster_label: 'Stoic Ethics' }, 1)).toBe('1 · Stoic Ethics')
    expect(stopCaption({ year: 1911, cluster_label: null }, 2)).toBe('2 · 1911')
    expect(stopCaption({ year: null, cluster_label: null }, 4)).toBe('4')
  })
})

describe('tourStep', () => {
  test('starts at the first stop', () => {
    expect(tourStep(null, 5)).toBe(0)
  })

  test('advances one at a time', () => {
    expect(tourStep(0, 5)).toBe(1)
    expect(tourStep(3, 5)).toBe(4)
  })

  test('is finished after the last stop', () => {
    expect(tourStep(4, 5)).toBeNull()
  })

  test('has nowhere to go on an empty trail', () => {
    expect(tourStep(null, 0)).toBeNull()
  })
})

describe('hopIsJump', () => {
  test('only the last row of an incomplete path', () => {
    expect(hopIsJump(3, 4, false)).toBe(true)
    expect(hopIsJump(2, 4, false)).toBe(false)
  })

  test('never on a complete path', () => {
    expect(hopIsJump(3, 4, true)).toBe(false)
  })

  test('a single stop has no hop to be a jump', () => {
    expect(hopIsJump(0, 1, false)).toBe(false)
  })
})

describe('stopsFromNodes', () => {
  test('builds stops from what the map already knows', () => {
    const stops = stopsFromNodes([20, 30], nodes, clusters)
    expect(stops).toEqual([
      {
        paper_id: 20,
        title: 'Paper 20',
        year: 1920,
        cluster_id: 3,
        cluster_label: 'Stoic Ethics',
        similarity_to_next: null,
        core_question: null,
      },
      {
        paper_id: 30,
        title: 'Paper 30',
        year: null,
        cluster_id: null,
        cluster_label: null,
        similarity_to_next: null,
        core_question: null,
      },
    ])
  })

  test('keeps the order it was given and drops unknown ids', () => {
    expect(stopsFromNodes([30, 99, 10], nodes, clusters).map((s) => s.paper_id)).toEqual([30, 10])
  })

  test('a cluster the map has no label for reads as unlabelled', () => {
    const stops = stopsFromNodes([20], nodes, [])
    expect(stops[0]?.cluster_label).toBeNull()
  })
})

describe('endIsSet', () => {
  test('a chosen paper is enough on its own', () => {
    expect(endIsSet({ paperId: 7, text: '' })).toBe(true)
  })

  test('a phrase must be long enough to anchor', () => {
    expect(endIsSet({ paperId: null, text: 'Ethics' })).toBe(true)
    expect(endIsSet({ paperId: null, text: ' a ' })).toBe(false)
    expect(endIsSet({ paperId: null, text: '' })).toBe(false)
  })
})

describe('pathQuery', () => {
  test('an id at each end', () => {
    expect(pathQuery({ paperId: 3, text: '' }, { paperId: 9, text: '' })).toBe('from=3&to=9')
  })

  test('phrases are trimmed and encoded', () => {
    expect(
      pathQuery({ paperId: null, text: ' Stoic ethics ' }, { paperId: null, text: 'free will' }),
    ).toBe('from_text=Stoic+ethics&to_text=free+will')
  })

  test('a chosen paper beats the phrase typed beside it', () => {
    expect(pathQuery({ paperId: 3, text: 'ignored' }, { paperId: null, text: 'Ethics' })).toBe(
      'from=3&to_text=Ethics',
    )
  })
})
