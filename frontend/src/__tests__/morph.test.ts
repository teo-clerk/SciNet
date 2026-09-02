/**
 * Morph arithmetic. The rule that keeps the view honest: a paper the
 * alternate run does not hold declines to travel — it keeps its active
 * position at every t, reading correctly as "the second model has no
 * opinion here", never as agreement.
 */
import { describe, expect, test } from 'bun:test'

import type { GraphNode } from '../api/graph'
import { buildTarget, farthestTravellers, lerpInto } from '../lib/morph'

const node = (id: number, title = `Paper ${id}`): GraphNode => ({
  id,
  title,
  year: null,
  cluster: null,
  tags: [],
  pages: null,
  confidence: null,
  provisional: false,
  drift: 0,
})

const NODES = [node(10), node(20), node(30)]
const BASE = new Float32Array([0, 0, 0, 1, 1, 1, 2, 2, 2])

describe('morph', () => {
  test('targets map by paper id, not by index', () => {
    const byId = new Map<number, [number, number, number]>([
      [20, [9, 9, 9]],
      [10, [5, 5, 5]],
    ])
    const target = buildTarget(NODES, BASE, byId)
    expect([...target.slice(0, 3)]).toEqual([5, 5, 5])
    expect([...target.slice(3, 6)]).toEqual([9, 9, 9])
  })

  test('a paper the alternate run lacks declines to travel', () => {
    const target = buildTarget(NODES, BASE, new Map())
    expect([...target]).toEqual([...BASE])
  })

  test('the lerp midpoint is the arithmetic middle', () => {
    const target = new Float32Array([2, 2, 2, 3, 3, 3, 4, 4, 4])
    const out = new Float32Array(9)
    lerpInto(out, BASE, target, 0.5)
    expect([...out.slice(0, 3)]).toEqual([1, 1, 1])
    lerpInto(out, BASE, target, 0)
    expect([...out]).toEqual([...BASE])
    lerpInto(out, BASE, target, 1)
    expect([...out]).toEqual([...target])
  })

  test('the farthest traveller is the biggest disagreement', () => {
    const byId = new Map<number, [number, number, number]>([
      [10, [0, 0, 0]], // stays put
      [20, [1, 1, 11]], // travels 10
      [30, [2, 2, 5]], // travels 3
    ])
    const target = buildTarget(NODES, BASE, byId)
    const [top, second] = farthestTravellers(NODES, BASE, target, 2)
    expect(top?.id).toBe(20)
    expect(top?.distance).toBeCloseTo(10)
    expect(second?.id).toBe(30)
  })
})
