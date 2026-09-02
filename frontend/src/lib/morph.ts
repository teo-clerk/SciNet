/**
 * Morph arithmetic, kept apart from the renderer so it can be tested
 * without WebGL.
 *
 * The target array lives in the ACTIVE run's index space: element i is where
 * node i should end up at t=1. A paper the alternate run does not hold keeps
 * its current position — it simply declines to travel, which reads correctly
 * as "the second model has no opinion here".
 */
import type { GraphNode } from '@/api/graph'

export function buildTarget(
  nodes: GraphNode[],
  base: Float32Array,
  byId: Map<number, [number, number, number]>,
): Float32Array {
  const target = new Float32Array(base.length)
  nodes.forEach((node, i) => {
    const alt = byId.get(node.id)
    if (alt) {
      target[i * 3] = alt[0]
      target[i * 3 + 1] = alt[1]
      target[i * 3 + 2] = alt[2]
    } else {
      target[i * 3] = base[i * 3]!
      target[i * 3 + 1] = base[i * 3 + 1]!
      target[i * 3 + 2] = base[i * 3 + 2]!
    }
  })
  return target
}

/** Write base*(1-t) + target*t into out, in place — the render hot path. */
export function lerpInto(
  out: Float32Array,
  base: Float32Array,
  target: Float32Array,
  t: number,
): void {
  for (let i = 0; i < out.length; i++) {
    out[i] = base[i]! * (1 - t) + target[i]! * t
  }
}

export interface Traveller {
  id: number
  title: string | null
  distance: number
}

/** The papers the two embedders disagree about most — the morph's headline. */
export function farthestTravellers(
  nodes: GraphNode[],
  base: Float32Array,
  target: Float32Array,
  limit = 5,
): Traveller[] {
  const travellers: Traveller[] = nodes.map((node, i) => {
    const dx = target[i * 3]! - base[i * 3]!
    const dy = target[i * 3 + 1]! - base[i * 3 + 1]!
    const dz = target[i * 3 + 2]! - base[i * 3 + 2]!
    return {
      id: node.id,
      title: node.title,
      distance: Math.sqrt(dx * dx + dy * dy + dz * dz),
    }
  })
  travellers.sort((a, b) => b.distance - a.distance)
  return travellers.slice(0, limit)
}
