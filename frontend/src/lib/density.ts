/**
 * How much of the map to draw, given how much of it there is.
 *
 * The map's chrome — cluster names, bridge curves — is legible at 57 papers
 * and illegible at 506 for the same reason a contour map is: the annotation
 * does not shrink with the data. Eight names on eight regions is a legend;
 * forty names over forty regions is a wall of overlapping text with the map
 * behind it, and the reader loses both.
 *
 * So the budget is fixed by the *viewport*, not by the corpus. Bridges are
 * capped by count, because each one crosses the whole map and a dozen of them
 * put the map behind a net however far you fly out.
 *
 * Labels are *not* capped here, and used to be. A count cap removes a name at
 * every zoom, so on a 16-region corpus four regions had no label the reader
 * could ever reach. Labels are culled against where they actually project
 * instead (see Labels.tsx), which is rationing the reader can undo by moving.
 */

/** Bridges drawn at once. Each is a curve across the whole map, so they cross
 *  each other rather than tiling: past a dozen the map is behind a net. */
export const MAX_BRIDGES = 10

/**
 * The strongest bridges, and how faintly to draw each one.
 *
 * Fading rather than a hard cut: a bridge that vanishes at some threshold
 * makes the map change shape as the corpus grows, and the reader cannot tell a
 * weak relationship from a missing one. The weakest drawn bridge is faint, the
 * strongest is full strength, and the ones past the budget are gone — which is
 * a statement the reader can act on, because the inspector still lists them.
 */
export function visibleBridges<T extends { similarity: number }>(
  links: T[],
  limit: number = MAX_BRIDGES,
): Array<{ link: T; weight: number }> {
  if (links.length === 0) return []

  const ranked = [...links].sort((a, b) => b.similarity - a.similarity)
  const drawn = ranked.slice(0, limit)

  const similarities = drawn.map((link) => link.similarity)
  const weakest = Math.min(...similarities)
  const span = Math.max(...similarities) - weakest

  return drawn.map((link) => ({
    link,
    // A single bridge, or a set that all agree, is drawn at full strength:
    // scaling by a span of zero would make every one of them the faintest.
    weight: span > 1e-6 ? 0.35 + 0.65 * ((link.similarity - weakest) / span) : 1,
  }))
}

/**
 * How much to shrink a node so a dense map does not read as a solid sheet.
 *
 * At 500 nodes in the volume that held 57, the points merge into fog well
 * before the frame budget is troubled — the limit is the eye, not the GPU.
 */
export function nodeScaleFor(count: number): number {
  if (count <= 100) return 1
  // Cube root: the map is a volume, so the space each node has to itself
  // shrinks with the cube root of how many are sharing it.
  return Math.max(0.45, Math.cbrt(100 / count))
}

/**
 * How brightly each node contributes, given how many are sharing the volume.
 *
 * The point cloud is drawn additively, so brightness is not a property of a
 * node — it is a property of how many nodes are behind it. A value tuned so
 * that one paper reads as solid makes thirty overlapping papers saturate to
 * flat white, which is exactly what a cluster core is: the densest and most
 * interesting part of the map, rendered as the part carrying no information.
 *
 * So the per-node contribution falls as the corpus grows. Square root, because
 * what accumulates along a view ray through a fixed volume grows roughly with
 * the linear density, not with the count.
 */
export function nodeIntensityFor(count: number, base: number): number {
  if (count <= REFERENCE_NODES) return base
  // Floored: below this a sparse region of a large map disappears entirely,
  // and the outliers are often the interesting ones.
  return Math.max(base * 0.45, base * Math.sqrt(REFERENCE_NODES / count))
}

/** The corpus the point styling was tuned against by eye. */
export const REFERENCE_NODES = 120
