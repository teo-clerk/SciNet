/**
 * How much of the map a meaning search lights up.
 *
 * Semantic search returns its k nearest whatever the query, so k decides
 * whether a result reads as "these papers" or as "nearly everything". The
 * limit follows the square root of the library, like the projection's own
 * parameters, between a floor a small library can still show and a ceiling
 * that bounds the payload.
 */
import { describe, expect, test } from 'bun:test'

import { FULLTEXT_LIMIT, semanticLimit } from '@/lib/search'

describe('semanticLimit', () => {
  test('an 85-paper benchmark lights up a fifth of the map, not two thirds', () => {
    expect(semanticLimit(85)).toBe(18)
  })

  test('a 500-paper library gets the neighbourhood the old fixed cap gave', () => {
    expect(semanticLimit(506)).toBe(45)
  })

  test('grows with the square root and never past the payload cap', () => {
    expect(semanticLimit(900)).toBe(FULLTEXT_LIMIT)
    expect(semanticLimit(4000)).toBe(FULLTEXT_LIMIT)
  })

  test('a tiny library still gets enough results to see', () => {
    expect(semanticLimit(9)).toBe(12)
    expect(semanticLimit(0)).toBe(12)
  })
})
