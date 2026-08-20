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
    tag_vocabulary: ['astrophysics', 'robotics'],
    clusters: [{ id: 1, label: 'Exoplanets', size: 2, terms: ['transit'] }],
    nodes: Array.from({ length: nodeCount }, (_, i) => ({
      id: i + 100,
      title: `Paper ${i}`,
      year: 2020 + i,
      cluster: 1,
      tags: [0],
      provisional: i === 1,
      drift: i === 1 ? 2.4 : 0.1,
    })),
  }
}

describe('decodeGraph', () => {
  test('recovers float32 positions exactly', () => {
    const values = [1.5, -2.25, 0.125, 10, 20, 30]
    const graph = decodeGraph(payload(values, 2))
    expect(Array.from(graph.positions)).toEqual(values)
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

  test('survives negative and fractional coordinates', () => {
    const values = [-0.0001, 12345.678, -9999.5]
    const graph = decodeGraph(payload(values, 1))
    expect(graph.positions[0]).toBeCloseTo(-0.0001, 6)
    expect(graph.positions[1]).toBeCloseTo(12345.678, 2)
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
