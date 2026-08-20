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
  const query = useGraphStore((s) => s.query)
  const activeTags = useGraphStore((s) => s.activeTags)

  return useMemo(() => {
    const trimmed = query.trim().toLowerCase()
    if (!trimmed && activeTags.size === 0) return null

    const visible = new Set<number>()
    nodes.forEach((node, i) => {
      if (trimmed && !(node.title ?? '').toLowerCase().includes(trimmed)) return
      // Tags are OR-ed: selecting two topics widens the view rather than
      // narrowing it to their intersection, which is almost always empty.
      if (activeTags.size > 0 && !node.tags.some((t) => activeTags.has(t))) return
      visible.add(i)
    })
    return visible
  }, [nodes, query, activeTags])
}
