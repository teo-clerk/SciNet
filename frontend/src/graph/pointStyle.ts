/**
 * Geometry shared by the visible point cloud and the picking pass.
 *
 * These must agree exactly. The picker renders the same points into an
 * offscreen target and reads back which one is under the cursor; if its sprite
 * size differs from the drawn one, the clickable area no longer matches what
 * the user sees, and clicks near the edge of a node land on nothing. Keeping
 * the value in one place makes that mismatch impossible rather than merely
 * unlikely.
 */

/** Sprite size before per-node scaling and perspective attenuation. */
export const BASE_POINT_SIZE = 5.0

/** Radius of a node's solid centre, as a fraction of the sprite. */
// The solid centre occupies the inner 30% of the sprite; everything beyond
// it is aura. Small enough to read as a point rather than a disc, large
// enough to stay visible when the camera pulls back.
export const CORE_RADIUS = 0.3

/** Per-node additive contribution; the picture is built by accumulation. */
// High enough that a lone node is solid, low enough that thirty overlapping
// sprites in a cluster core do not saturate to white.
export const POINT_INTENSITY = 0.72
