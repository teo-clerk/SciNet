/**
 * Binary payload decoding and pick-index encoding.
 *
 * Both are places where an off-by-one or an endianness slip produces something
 * that *looks* fine — a map drawn at slightly wrong coordinates, or a cursor
 * that selects the neighbouring paper — so they are checked explicitly.
 */
import { describe, expect, test } from 'bun:test'

import { decodeGraph, type GraphPayload } from '../api/graph'

function encode(values: number[]): string {
  const floats = new Float32Array(values)
  const bytes = new Uint8Array(floats.buffer)
  let binary = ''
  for (const b of bytes) binary += String.fromCharCode(b)
  return btoa(binary)
}

function payload(positions: number[], nodeCount: number): GraphPayload {
  return {
    run_id: 7,
    method: 'umap',
    count: nodeCount,
    positions_f32: encode(positions),
    skeleton_u32: '',
    skeleton_edges: 0,
    tag_vocabulary: ['astrophysics', 'robotics'],
    clusters: [{ id: 1, label: 'Exoplanets', size: 2, terms: ['transit'] }],
    nodes: Array.from({ length: nodeCount }, (_, i) => ({
      id: i + 100,
      title: `Paper ${i}`,
      year: 2020 + i,
      cluster: 1,
      tags: [0],
      pages: 10 + i,
      provisional: i === 1,
      drift: i === 1 ? 2.4 : 0.1,
    })),
  }
}

describe('decodeGraph', () => {
  test('recovers the layout, centred and scaled for display', () => {
    // decodeGraph normalises: raw UMAP output is offset from the origin and
    // arbitrarily scaled, and every renderer constant assumes a known extent.
    const values = [1.5, -2.25, 0.125, 10, 20, 30]
    const graph = decodeGraph(payload(values, 2))
    expect(graph.positions.length).toBe(6)

    // Structure survives: the two points stay opposite each other about the
    // centre, and their separation keeps its direction on every axis.
    const [ax, ay, az, bx, by, bz] = Array.from(graph.positions)
    expect(Math.sign(bx! - ax!)).toBe(Math.sign(10 - 1.5))
    expect(Math.sign(by! - ay!)).toBe(Math.sign(20 - -2.25))
    expect(Math.sign(bz! - az!)).toBe(Math.sign(30 - 0.125))
  })

  test('relative distances survive normalisation', () => {
    // Three collinear points 1 and 10 apart must stay 1:10 after scaling,
    // or the map would misrepresent which papers are close.
    const graph = decodeGraph(payload([0, 0, 0, 1, 0, 0, 10, 0, 0], 3))
    const p = graph.positions
    const near = Math.abs(p[3]! - p[0]!)
    const far = Math.abs(p[6]! - p[0]!)
    expect(far / near).toBeCloseTo(10, 3)
  })

  test('produces three floats per node', () => {
    const graph = decodeGraph(payload([0, 0, 0, 1, 1, 1, 2, 2, 2], 3))
    expect(graph.positions.length).toBe(9)
    expect(graph.nodes.length).toBe(3)
  })

  test('rejects a payload whose positions do not match its node count', () => {
    // A silent mismatch would draw the map at shifted coordinates.
    expect(() => decodeGraph(payload([1, 2, 3], 5))).toThrow(/inconsistent/)
  })

  test('carries run metadata through', () => {
    const graph = decodeGraph(payload([0, 0, 0], 1))
    expect(graph.runId).toBe(7)
    expect(graph.method).toBe('umap')
    expect(graph.clusters[0]?.label).toBe('Exoplanets')
    expect(graph.tagVocabulary).toEqual(['astrophysics', 'robotics'])
  })

  test('preserves the provisional flag and drift', () => {
    const graph = decodeGraph(payload([0, 0, 0, 1, 1, 1], 2))
    expect(graph.nodes[0]?.provisional).toBe(false)
    expect(graph.nodes[1]?.provisional).toBe(true)
    expect(graph.nodes[1]?.drift).toBe(2.4)
  })

  test('handles an empty corpus', () => {
    const graph = decodeGraph(payload([], 0))
    expect(graph.positions.length).toBe(0)
    expect(graph.nodes).toEqual([])
  })

  test('survives extreme coordinate ranges', () => {
    // UMAP has no fixed output range; a corpus can land anywhere.
    const graph = decodeGraph(payload([-0.0001, 12345.678, -9999.5], 1))
    expect([...graph.positions].every(Number.isFinite)).toBe(true)
  })
})

describe('pick index encoding', () => {
  // Mirrors PointCloud.tsx: index + 1 packed little-endian into RGB, so that
  // 0 can mean "cleared background".
  const encodeIndex = (i: number) => [
    ((i + 1) & 0xff) / 255,
    (((i + 1) >> 8) & 0xff) / 255,
    (((i + 1) >> 16) & 0xff) / 255,
  ]
  const decodePixel = (r: number, g: number, b: number) => {
    const v = Math.round(r * 255) + (Math.round(g * 255) << 8) + (Math.round(b * 255) << 16)
    return v === 0 ? null : v - 1
  }

  test('round-trips across byte boundaries', () => {
    for (const i of [0, 1, 254, 255, 256, 257, 65535, 65536, 999999]) {
      const [r, g, b] = encodeIndex(i)
      expect(decodePixel(r!, g!, b!)).toBe(i)
    }
  })

  test('a cleared pixel means no node, not node zero', () => {
    // The offset-by-one exists precisely so this case is unambiguous.
    expect(decodePixel(0, 0, 0)).toBeNull()
    expect(decodePixel(...(encodeIndex(0) as [number, number, number]))).toBe(0)
  })

  test('covers the full corpus range without collision', () => {
    const seen = new Set<string>()
    for (let i = 0; i < 5000; i++) seen.add(encodeIndex(i).join(','))
    expect(seen.size).toBe(5000)
  })
})

describe('normalisePositions', () => {
  test('centres the layout on the origin', async () => {
    const { normalisePositions } = await import('../api/graph')
    // Mirrors the real run: offset from origin, never near it.
    const raw = new Float32Array([6, 2, -4, 10, 8, 2, 8, 5, -1])
    const out = normalisePositions(raw)

    let cx = 0, cy = 0, cz = 0
    for (let i = 0; i < 3; i++) {
      cx += out[i * 3]!; cy += out[i * 3 + 1]!; cz += out[i * 3 + 2]!
    }
    expect(Math.abs(cx / 3)).toBeLessThan(1e-4)
    expect(Math.abs(cy / 3)).toBeLessThan(1e-4)
    expect(Math.abs(cz / 3)).toBeLessThan(1e-4)
  })

  test('scales to the canonical radius', async () => {
    const { normalisePositions, CANONICAL_RADIUS } = await import('../api/graph')
    const out = normalisePositions(new Float32Array([0, 0, 0, 1, 0, 0, -1, 0, 0]))

    let maxR = 0
    for (let i = 0; i < 3; i++) {
      const r = Math.hypot(out[i * 3]!, out[i * 3 + 1]!, out[i * 3 + 2]!)
      if (r > maxR) maxR = r
    }
    expect(maxR).toBeCloseTo(CANONICAL_RADIUS, 3)
  })

  test('preserves relative structure', async () => {
    const { normalisePositions } = await import('../api/graph')
    // Two points close together, one far: the ratio must survive scaling.
    const raw = new Float32Array([0, 0, 0, 1, 0, 0, 10, 0, 0])
    const out = normalisePositions(raw)
    const near = Math.abs(out[3]! - out[0]!)
    const far = Math.abs(out[6]! - out[0]!)
    expect(far / near).toBeCloseTo(10, 3)
  })

  test('a degenerate corpus does not divide by zero', async () => {
    const { normalisePositions } = await import('../api/graph')
    // Every paper at the same point — possible with one paper, or duplicates.
    const out = normalisePositions(new Float32Array([5, 5, 5, 5, 5, 5]))
    expect([...out].every(Number.isFinite)).toBe(true)
  })

  test('an empty corpus is returned untouched', async () => {
    const { normalisePositions } = await import('../api/graph')
    expect(normalisePositions(new Float32Array([])).length).toBe(0)
  })
})
