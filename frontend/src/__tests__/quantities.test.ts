/**
 * Quantity formatting: canonical units in, human scales out. "705000 meter"
 * is technically true and practically rude.
 */
import { describe, expect, test } from 'bun:test'

import { formatQuantity } from '../lib/quantities'

describe('formatQuantity', () => {
  test('lengths pick the scale a person would have written', () => {
    expect(formatQuantity(705000, 'meter', 'length')).toBe('705 km')
    expect(formatQuantity(0.5, 'meter', 'length')).toBe('50 cm')
    expect(formatQuantity(5e-7, 'meter', 'length')).toBe('500 nm')
  })

  test('frequency and power scale likewise', () => {
    expect(formatQuantity(5.405e9, 'hertz', 'frequency')).toBe('5.41 GHz')
    expect(formatQuantity(2400, 'watt', 'power')).toBe('2.4 kW')
  })

  test('fractions read as percent and levels as dB', () => {
    expect(formatQuantity(0.12, 'dimensionless', 'fraction')).toBe('12 %')
    expect(formatQuantity(23, 'dB', 'level')).toBe('23 dB')
  })

  test('a missing value is a question mark, never a zero', () => {
    expect(formatQuantity(null, null, 'length')).toBe('?')
  })

  test('unknown kinds fall back to sane notation', () => {
    expect(formatQuantity(1.9224e-13, 'joule', 'energy')).toBe('1.92e-13 joule')
  })
})
