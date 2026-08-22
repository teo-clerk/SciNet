/**
 * Rationing the map's chrome as the corpus grows.
 *
 * The failure this prevents is not a crash: 506 nodes render at 230 fps. It is
 * that forty cluster names over forty regions is a wall of overlapping text
 * with the map behind it, and the reader loses both the names and the map.
 */
import { describe, expect, test } from 'bun:test'

import {
  MAX_BRIDGES,
  MAX_LABELS,
  nodeScaleFor,
  prominentClusters,
  visibleBridges,
} from '../lib/density'

const clusters = (sizes: number[]) => sizes.map((size, id) => ({ id, size }))
const links = (sims: number[]) => sims.map((similarity) => ({ similarity }))

describe('label rationing', () => {
  test('a small map shows every name', () => {
    const shown = prominentClusters(clusters([9, 4, 12, 3]))

    expect(shown.size).toBe(4)
  })

  test('a crowded map keeps the largest regions', () => {
    const sizes = Array.from({ length: 40 }, (_, i) => i + 1)
    const shown = prominentClusters(clusters(sizes))

    expect(shown.size).toBe(MAX_LABELS)
    // Ids are indices, so the largest are the last.
    expect(shown.has(39)).toBe(true)
    expect(shown.has(0)).toBe(false)
  })

  test('size decides, not order', () => {
    const shown = prominentClusters(clusters([80, 1, 1, 1]), 1)

    expect([...shown]).toEqual([0])
  })

  test('an empty map is not an error', () => {
    expect(prominentClusters([]).size).toBe(0)
  })
})

describe('bridge rationing', () => {
  test('few bridges are all drawn', () => {
    expect(visibleBridges(links([0.9, 0.8, 0.7]))).toHaveLength(3)
  })

  test('a crowded map keeps the strongest', () => {
    const drawn = visibleBridges(links(Array.from({ length: 30 }, (_, i) => i / 30)))

    expect(drawn).toHaveLength(MAX_BRIDGES)
    expect(drawn.map((d) => d.link.similarity)).toContain(29 / 30)
  })

  test('the strongest is full strength and the weakest is faint', () => {
    const drawn = visibleBridges(links([0.9, 0.5, 0.1]))

    expect(drawn.map((d) => d.weight)).toEqual([1, 0.675, 0.35])
  })

  test('bridges that all agree are not all drawn as the faintest', () => {
    // A zero span would divide by zero and render every bridge at the floor.
    const drawn = visibleBridges(links([0.7, 0.7, 0.7]))

    expect(drawn.every((d) => d.weight === 1)).toBe(true)
  })

  test('a single bridge is drawn at full strength', () => {
    expect(visibleBridges(links([0.42])).map((d) => d.weight)).toEqual([1])
  })

  test('no bridges is not an error', () => {
    expect(visibleBridges([])).toEqual([])
  })
})

describe('node scaling', () => {
  test('a sparse map is untouched', () => {
    expect(nodeScaleFor(57)).toBe(1)
  })

  test('nodes shrink as the volume fills', () => {
    expect(nodeScaleFor(506)).toBeLessThan(1)
    expect(nodeScaleFor(5000)).toBeLessThan(nodeScaleFor(506))
  })

  test('they never shrink to invisibility', () => {
    expect(nodeScaleFor(100000)).toBeGreaterThanOrEqual(0.45)
  })
})
