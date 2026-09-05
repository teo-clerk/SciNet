/**
 * The sentences that stand in for the numbers when the lab is off.
 *
 * Each says only what its number supports. A reader who sees "sits firmly in
 * Fluid Mechanics" has been told the confidence was high, in the words they
 * would use themselves; nobody has to know what 0.5 means.
 */
import { describe, expect, test } from 'bun:test'

import {
  entityGroups,
  genreLabel,
  KIND_LABELS,
  pagesLabel,
  placementSentence,
} from '../lib/plain'

describe('placementSentence', () => {
  test('a confident paper sits firmly', () => {
    expect(placementSentence('Fluid Mechanics', 0.82, 1.0)).toBe(
      'Sits firmly in Fluid Mechanics',
    )
  })

  test('the threshold is inclusive at 0.5', () => {
    expect(placementSentence('X', 0.5, null)).toBe('Sits firmly in X')
    expect(placementSentence('X', 0.49, null)).toBe(
      'Sits between X and its neighbours',
    )
  })

  test('a paper the map barely knows says so, after its region', () => {
    expect(placementSentence('Turbulence', 0.3, 2.1)).toBe(
      'Sits between Turbulence and its neighbours · Unlike most of the library',
    )
  })

  test('drift alone still says something', () => {
    // Unclustered but far from everything: the reader deserves to know why it
    // is out on its own.
    expect(placementSentence(null, null, 1.7)).toBe('Unlike most of the library')
  })

  test('typical drift is not remarked on', () => {
    expect(placementSentence(null, null, 1.6)).toBeNull()
    expect(placementSentence(null, null, 0.9)).toBeNull()
  })

  test('a region with no confidence is not called firm', () => {
    expect(placementSentence('X', null, null)).toBe('Sits in X')
  })

  test('nothing to say is null, not an empty string', () => {
    expect(placementSentence(null, null, null)).toBeNull()
  })
})

describe('pagesLabel', () => {
  test('pluralises', () => {
    expect(pagesLabel(18)).toBe('18 pages')
    expect(pagesLabel(1)).toBe('1 page')
  })

  test('unknown or nonsensical counts are omitted', () => {
    expect(pagesLabel(null)).toBeNull()
    expect(pagesLabel(0)).toBeNull()
  })
})

describe('genreLabel', () => {
  test('maps every genre the classifier emits', () => {
    expect(genreLabel('empirical-study')).toBe('Empirical study')
    expect(genreLabel('theoretical-argument')).toBe('Theoretical argument')
    expect(genreLabel('survey-or-review')).toBe('Survey')
    expect(genreLabel('essay-or-commentary')).toBe('Essay')
    expect(genreLabel('historical-narrative')).toBe('History')
    expect(genreLabel('technical-report')).toBe('Technical report')
    expect(genreLabel('literary-or-fiction')).toBe('Literary work')
    expect(genreLabel('reference-or-textbook')).toBe('Reference')
  })

  test('"other" and unknown are no chip at all', () => {
    expect(genreLabel('other')).toBeNull()
    expect(genreLabel(null)).toBeNull()
    expect(genreLabel('limerick')).toBeNull()
  })
})

describe('entityGroups', () => {
  const entities = [
    { name: 'Cambridge', kind: 'place' },
    { name: 'Turing', kind: 'person' },
    { name: 'Entscheidungsproblem', kind: 'concept' },
    { name: 'On Computable Numbers', kind: 'work' },
    { name: 'Church', kind: 'person' },
    { name: 'Bletchley Park', kind: 'organisation' },
  ]

  test('orders people, works, ideas, events, places, organisations', () => {
    expect(entityGroups(entities).map(([kind]) => kind)).toEqual([
      'person',
      'work',
      'concept',
      'place',
      'organisation',
    ])
  })

  test('keeps the names in the order found, within a kind', () => {
    const people = entityGroups(entities).find(([kind]) => kind === 'person')
    expect(people?.[1]).toEqual(['Turing', 'Church'])
  })

  test('drops empty kinds and unknown ones', () => {
    expect(entityGroups([{ name: 'x', kind: 'mood' }])).toEqual([])
    expect(entityGroups([])).toEqual([])
  })

  test('every ordered kind has a heading', () => {
    for (const [kind] of entityGroups(entities)) {
      expect(KIND_LABELS[kind]).toBeDefined()
    }
    expect(KIND_LABELS.concept).toBe('Ideas')
  })
})
