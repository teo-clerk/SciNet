/**
 * When "where do I start?" is a question worth asking.
 *
 * One paper has no starting point but itself. Past a couple of thousand the
 * question stops being about the filtered set and starts being about the
 * whole library, which is what the region inspectors already answer — and the
 * request would carry every id on the map.
 */

export const ENTRY_POINT_MIN = 2
export const ENTRY_POINT_MAX = 2000

export function canAskWhereToStart(visibleCount: number): boolean {
  return visibleCount >= ENTRY_POINT_MIN && visibleCount <= ENTRY_POINT_MAX
}
