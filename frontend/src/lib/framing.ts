/**
 * Where the camera should sit to frame a set of nodes.
 *
 * Kept apart from CameraRig so the arithmetic is testable without a camera:
 * centroid of the set, its bounding radius, and a viewing distance that
 * scales with the spread — one cited paper wants the single-paper distance,
 * a dozen scattered ones want to be seen together.
 */

/** Matches CameraRig's single-paper distance. */
const MIN_DISTANCE = 14
const MAX_DISTANCE = 120
const SPREAD_FACTOR = 2.2

export interface Framing {
  target: [number, number, number]
  distance: number
}

export function frameForSet(
  indices: number[],
  positions: Float32Array,
): Framing | null {
  if (indices.length === 0) return null
  let cx = 0
  let cy = 0
  let cz = 0
  for (const i of indices) {
    cx += positions[i * 3]!
    cy += positions[i * 3 + 1]!
    cz += positions[i * 3 + 2]!
  }
  cx /= indices.length
  cy /= indices.length
  cz /= indices.length

  let radius = 0
  for (const i of indices) {
    const dx = positions[i * 3]! - cx
    const dy = positions[i * 3 + 1]! - cy
    const dz = positions[i * 3 + 2]! - cz
    radius = Math.max(radius, Math.sqrt(dx * dx + dy * dy + dz * dz))
  }

  const distance = Math.min(
    MAX_DISTANCE,
    Math.max(MIN_DISTANCE, radius * SPREAD_FACTOR + MIN_DISTANCE),
  )
  return { target: [cx, cy, cz], distance }
}
