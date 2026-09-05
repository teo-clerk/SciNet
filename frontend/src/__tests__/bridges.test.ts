/**
 * A region's bridges, resolved to "here" and "there".
 *
 * Which end the server stored as the source is an accident of ordering; the
 * inspector must show the same list whichever side its region landed on.
 */
import { describe, expect, test } from 'bun:test'

import type { ClusterLink } from '../api/clusters'
import { bridgesFrom } from '../lib/bridges'

const link = (
  source: number,
  target: number,
  similarity: number,
  summary: string | null = null,
): ClusterLink => ({
  source_id: source,
  target_id: target,
  source_label: `Region ${source}`,
  target_label: `Region ${target}`,
  similarity,
  shared_terms: [],
  summary,
  bridge_papers: [],
})

const links = [link(1, 2, 0.4, 'shared methods'), link(3, 1, 0.7), link(2, 3, 0.9)]

describe('bridgesFrom', () => {
  test('finds the region at either end of a link', () => {
    expect(bridgesFrom(links, 1).map((b) => b.otherId)).toEqual([3, 2])
  })

  test('names the other side, not this one', () => {
    const [strongest] = bridgesFrom(links, 1)
    expect(strongest?.otherLabel).toBe('Region 3')
  })

  test('strongest first', () => {
    expect(bridgesFrom(links, 2).map((b) => b.similarity)).toEqual([0.9, 0.4])
  })

  test('carries the summary through', () => {
    expect(bridgesFrom(links, 2).find((b) => b.otherId === 1)?.summary).toBe('shared methods')
  })

  test('a region with no bridges has an empty list', () => {
    expect(bridgesFrom(links, 9)).toEqual([])
  })
})
