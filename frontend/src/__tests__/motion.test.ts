/**
 * Reduced motion, asked from script.
 *
 * bun has no matchMedia, which is exactly the case the guard exists for: a
 * module that touched it at load would take every other test down with it.
 */
import { describe, expect, test } from 'bun:test'

import { prefersReducedMotion } from '../lib/motion'

describe('prefersReducedMotion', () => {
  test('is false where there is no matchMedia', () => {
    expect(prefersReducedMotion()).toBe(false)
    expect(prefersReducedMotion(undefined)).toBe(false)
  })

  test('reports what matchMedia says', () => {
    expect(prefersReducedMotion(() => ({ matches: true }))).toBe(true)
    expect(prefersReducedMotion(() => ({ matches: false }))).toBe(false)
  })

  test('asks the right question', () => {
    const asked: string[] = []
    prefersReducedMotion((query) => {
      asked.push(query)
      return { matches: false }
    })
    expect(asked).toEqual(['(prefers-reduced-motion: reduce)'])
  })

  test('a matchMedia that throws reads as no preference', () => {
    expect(
      prefersReducedMotion(() => {
        throw new TypeError('Illegal invocation')
      }),
    ).toBe(false)
  })
})
