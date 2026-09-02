/**
 * Which nodes survive the current filters.
 *
 * Returns a Set of indices, or null meaning "everything" — the null case lets
 * the renderer skip per-node work entirely when no filter is active, which is
 * the common case while orbiting.
 */
import { useMemo } from 'react'

import { useGraphStore } from '@/state/graphStore'

export function useVisibleSet(): Set<number> | null {
  const nodes = useGraphStore((s) => s.nodes)
  const activeTags = useGraphStore((s) => s.activeTags)
  const searchResults = useGraphStore((s) => s.searchResults)
  const yearCutoff = useGraphStore((s) => s.yearCutoff)

  return useMemo(() => {
    if (searchResults === null && activeTags.size === 0 && yearCutoff === null) {
      return null
    }

    // Search returns paper ids; the renderer works in node indices.
    const matchedIndices =
      searchResults === null
        ? null
        : new Set(
            nodes.reduce<number[]>((acc, node, i) => {
              if (searchResults.has(node.id)) acc.push(i)
              return acc
            }, []),
          )

    const visible = new Set<number>()
    nodes.forEach((node, i) => {
      if (matchedIndices !== null && !matchedIndices.has(i)) return
      // Tags are OR-ed: selecting two topics widens the view rather than
      // narrowing it to their intersection, which is almost always empty.
      if (activeTags.size > 0 && !node.tags.some((t) => activeTags.has(t))) return
      // The cutoff hides what came after. An unknown year is not a "later"
      // year, so undated papers stay visible at every scrubber position.
      if (yearCutoff !== null && node.year !== null && node.year > yearCutoff) return
      visible.add(i)
    })
    return visible
  }, [nodes, activeTags, searchResults, yearCutoff])
}
