/**
 * Ordering for the list view.
 *
 * Kept apart from the component so the comparison rules can be tested without
 * rendering anything, and so the map and the list agree on what "the same set
 * of papers" means.
 */
import type { GraphCluster, GraphNode } from '@/api/graph'
import type { SortKey } from '@/state/graphStore'

export interface ListRow {
  index: number
  node: GraphNode
  clusterName: string | null
}

export function buildRows(
  nodes: GraphNode[],
  clusters: GraphCluster[],
  visible: Set<number> | null,
): ListRow[] {
  const nameOf = new Map(clusters.map((c) => [c.id, c.label]))
  const rows: ListRow[] = []
  nodes.forEach((node, index) => {
    if (visible !== null && !visible.has(index)) return
    rows.push({
      index,
      node,
      clusterName: node.cluster === null ? null : (nameOf.get(node.cluster) ?? null),
    })
  })
  return rows
}

/** Sorts in place and returns the same array. */
export function sortRows(rows: ListRow[], key: SortKey, ascending: boolean): ListRow[] {
  const direction = ascending ? 1 : -1

  rows.sort((a, b) => {
    switch (key) {
      case 'year': {
        // Papers with no year sink to the bottom either way rather than
        // clumping at whichever end the sort direction happens to favour.
        const x = a.node.year
        const y = b.node.year
        if (x === null && y === null) return byTitle(a, b)
        if (x === null) return 1
        if (y === null) return -1
        return (x - y) * direction || byTitle(a, b)
      }
      case 'confidence': {
        const x = a.node.confidence
        const y = b.node.confidence
        if (x === null && y === null) return byTitle(a, b)
        if (x === null) return 1
        if (y === null) return -1
        return (x - y) * direction || byTitle(a, b)
      }
      case 'cluster': {
        const x = a.clusterName ?? ''
        const y = b.clusterName ?? ''
        if (!x && !y) return byTitle(a, b)
        if (!x) return 1
        if (!y) return -1
        return x.localeCompare(y) * direction || byTitle(a, b)
      }
      default:
        return byTitle(a, b) * direction
    }
  })
  return rows
}

function byTitle(a: ListRow, b: ListRow): number {
  return (a.node.title ?? '￿').localeCompare(b.node.title ?? '￿')
}
