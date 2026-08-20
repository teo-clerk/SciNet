/**
 * List ordering.
 *
 * The list and the map show the same papers under the same filters, so the
 * rules that decide membership and order are tested apart from the rendering.
 */
import { describe, expect, test } from 'bun:test'

import type { GraphCluster, GraphNode } from '../api/graph'
import { buildRows, sortRows } from '../lib/paperList'

function node(over: Partial<GraphNode> & { id: number }): GraphNode {
  return {
    title: `Paper ${over.id}`, year: 2020, cluster: 1, tags: [], pages: 10,
    confidence: 0.9, provisional: false, drift: 0, ...over,
  }
}

const CLUSTERS: GraphCluster[] = [
  { id: 1, label: 'Astrophysics', size: 3, terms: [] },
  { id: 2, label: 'Robotics', size: 2, terms: [] },
]

const NODES = [
  node({ id: 1, title: 'Zebra Dynamics', year: 2019, cluster: 2, confidence: 0.4 }),
  node({ id: 2, title: 'Alpha Particles', year: 2021, cluster: 1, confidence: 0.95 }),
  node({ id: 3, title: 'Middle Ground', year: null, cluster: null, confidence: null }),
]

describe('buildRows', () => {
  test('includes every paper when nothing is filtered', () => {
    expect(buildRows(NODES, CLUSTERS, null)).toHaveLength(3)
  })

  test('honours the same filter the map uses', () => {
    const rows = buildRows(NODES, CLUSTERS, new Set([0, 2]))
    expect(rows.map((r) => r.node.id)).toEqual([1, 3])
  })

  test('resolves cluster names', () => {
    const rows = buildRows(NODES, CLUSTERS, null)
    expect(rows[0]?.clusterName).toBe('Robotics')
    expect(rows[1]?.clusterName).toBe('Astrophysics')
  })

  test('an unclustered paper has no region name', () => {
    expect(buildRows(NODES, CLUSTERS, null)[2]?.clusterName).toBeNull()
  })

  test('keeps the map index so selection stays in sync', () => {
    const rows = buildRows(NODES, CLUSTERS, new Set([2]))
    expect(rows[0]?.index).toBe(2)
  })

  test('an empty filter yields no rows', () => {
    expect(buildRows(NODES, CLUSTERS, new Set())).toHaveLength(0)
  })
})

describe('sortRows', () => {
  const rows = () => buildRows(NODES, CLUSTERS, null)

  test('sorts by title', () => {
    const sorted = sortRows(rows(), 'title', true)
    expect(sorted.map((r) => r.node.title)).toEqual([
      'Alpha Particles', 'Middle Ground', 'Zebra Dynamics',
    ])
  })

  test('reverses on demand', () => {
    const sorted = sortRows(rows(), 'title', false)
    expect(sorted[0]?.node.title).toBe('Zebra Dynamics')
  })

  test('sorts by year', () => {
    expect(sortRows(rows(), 'year', true).map((r) => r.node.year)).toEqual([
      2019, 2021, null,
    ])
  })

  test('papers without a year sink to the bottom in both directions', () => {
    // Otherwise they clump at whichever end the direction happens to favour
    // and look like the oldest or newest work in the library.
    for (const ascending of [true, false]) {
      const sorted = sortRows(rows(), 'year', ascending)
      expect(sorted[sorted.length - 1]?.node.year).toBeNull()
    }
  })

  test('unclustered papers sink to the bottom when sorting by region', () => {
    for (const ascending of [true, false]) {
      const sorted = sortRows(rows(), 'cluster', ascending)
      expect(sorted[sorted.length - 1]?.clusterName).toBeNull()
    }
  })

  test('sorts by confidence', () => {
    const sorted = sortRows(rows(), 'confidence', false)
    expect(sorted[0]?.node.confidence).toBe(0.95)
  })

  test('ties fall back to the title so the order is stable', () => {
    const tied = [
      node({ id: 10, title: 'Beta', year: 2020, confidence: 0.5 }),
      node({ id: 11, title: 'Alpha', year: 2020, confidence: 0.5 }),
    ]
    const sorted = sortRows(buildRows(tied, CLUSTERS, null), 'year', true)
    expect(sorted.map((r) => r.node.title)).toEqual(['Alpha', 'Beta'])
  })
})
