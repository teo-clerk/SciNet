/**
 * M0 scaffold data.
 *
 * Generates a plausible cluster structure so the rendering budget can be proven
 * before the ingestion pipeline exists. Replaced by the binary /api/graph
 * payload at M3.
 */
import { CLUSTER_PALETTE, type GraphBuffers, type NodeMeta } from '@/state/graphStore'

/** Box-Muller — a normal sample, so clusters look like real embedding blobs. */
function gaussian(rand: () => number): number {
  const u = 1 - rand()
  const v = rand()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

/** Deterministic PRNG so reloads produce the same map. */
function mulberry32(seed: number): () => number {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function makeMockGraph(
  count = 4000,
  clusterCount = 9,
): { buffers: GraphBuffers; meta: NodeMeta[] } {
  const rand = mulberry32(1337)

  const positions = new Float32Array(count * 3)
  const colors = new Float32Array(count * 3)
  const sizes = new Float32Array(count)
  const filtered = new Float32Array(count)
  const selected = new Float32Array(count)
  const meta: NodeMeta[] = []

  // Cluster centroids spread over a sphere.
  const centroids: [number, number, number][] = []
  for (let c = 0; c < clusterCount; c++) {
    const theta = rand() * Math.PI * 2
    const phi = Math.acos(2 * rand() - 1)
    const r = 22 + rand() * 10
    centroids.push([
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.sin(phi) * Math.sin(theta),
      r * Math.cos(phi),
    ])
  }

  for (let i = 0; i < count; i++) {
    const c = Math.floor(rand() * clusterCount)
    const centroid = centroids[c]!
    const spread = 3.5 + rand() * 3.5

    positions[i * 3 + 0] = centroid[0] + gaussian(rand) * spread
    positions[i * 3 + 1] = centroid[1] + gaussian(rand) * spread
    positions[i * 3 + 2] = centroid[2] + gaussian(rand) * spread

    const rgb = CLUSTER_PALETTE[c % CLUSTER_PALETTE.length]!
    colors[i * 3 + 0] = rgb[0]
    colors[i * 3 + 1] = rgb[1]
    colors[i * 3 + 2] = rgb[2]

    sizes[i] = 0.75 + rand() * 0.6
    filtered[i] = 1
    selected[i] = 0

    meta.push({
      id: i,
      title: `Placeholder paper #${i}`,
      clusterId: c,
      year: 2005 + Math.floor(rand() * 21),
    })
  }

  return { buffers: { positions, colors, sizes, filtered, selected }, meta }
}
