/**
 * Rationing the map's chrome as the corpus grows.
 *
 * The failure this prevents is not a crash: 506 nodes render at 230 fps. It is
 * that forty cluster names over forty regions is a wall of overlapping text
 * with the map behind it, and the reader loses both the names and the map.
 */
import { describe, expect, test } from 'bun:test'

import { MAX_BRIDGES, nodeScaleFor, visibleBridges } from '../lib/density'

const links = (sims: number[]) => sims.map((similarity) => ({ similarity }))

describe('labels are not rationed by count', () => {
  test('density exports no label cap', async () => {
    // Regression. A count cap removed a name at *every* zoom: on the 16-region
    // corpus the four smallest — Cognitive Neuroscience, Epithelial Mechanics,
    // Neuroscience, Epigenetic Development — had labels in the database and in
    // the API payload, and no way to reach them in the map however far the
    // reader flew in. Labels are culled against where they actually project
    // instead, which the reader can undo by moving.
    const density = await import('../lib/density')

    expect('MAX_LABELS' in density).toBe(false)
    expect('prominentClusters' in density).toBe(false)
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
