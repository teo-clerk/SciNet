/**
 * Model Lab arithmetic. The gauge's one rule: understate, never invent — a
 * resident model with no measured footprint contributes zero, and the
 * "unproven" badge beside it says why the bar looks short.
 */
import { describe, expect, test } from 'bun:test'

import type { ModelProfileInfo } from '../api/models'
import { estimateResidentMib, formatMib, gaugeFraction } from '../lib/modelLab'

const profile = (reference: string, vram: number | null): ModelProfileInfo => ({
  reference,
  runtime: 'ollama',
  role: 'tag',
  disk_mib: null,
  vram_mib: vram,
  tok_per_s: null,
  embed_dim: null,
  verdict: vram === null ? 'unproven' : 'gpu',
  source: 'builtin',
  notes: null,
  fits_here: null,
})

describe('modelLab', () => {
  test('mib formats to G above a gigabyte and M below', () => {
    expect(formatMib(5673)).toBe('5.54G')
    expect(formatMib(768)).toBe('768M')
    expect(formatMib(null)).toBe('?')
  })

  test('the gauge sums only measured residents', () => {
    const profiles = [profile('a:1', 5673), profile('b:1', null)]
    expect(estimateResidentMib(['a:1', 'b:1'], profiles)).toBe(5673)
  })

  test('an unknown resident contributes nothing rather than guessing', () => {
    expect(estimateResidentMib(['ghost:1'], [profile('a:1', 100)])).toBe(0)
  })

  test('the gauge clamps at full — an overfull card still reads 100%', () => {
    expect(gaugeFraction(9000, 8188)).toBe(1)
    expect(gaugeFraction(4094, 8188)).toBeCloseTo(0.5)
    expect(gaugeFraction(100, null)).toBe(0)
  })
})
