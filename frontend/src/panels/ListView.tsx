/**
 * The library as a table.
 *
 * The map answers "what is near what"; this answers "what do I have", which is
 * a different question and a poor fit for a point cloud — you cannot scan a
 * constellation alphabetically. Both views read the same filter state and open
 * the same detail panel, so switching never loses the reader's place.
 */
import { useMemo } from 'react'

import { buildRows, sortRows } from '@/lib/paperList'
import { useVisibleSet } from '@/lib/filtering'
import { useGraphStore, type SortKey } from '@/state/graphStore'

const COLUMNS: Array<[SortKey, string, string]> = [
  ['title', 'Paper', 'title'],
  ['year', 'Year', 'year'],
  ['cluster', 'Region', 'region'],
]

/** A decimal in a table is for whoever is tuning the clustering; the lab owns it. */
const LAB_COLUMNS: Array<[SortKey, string, string]> = [
  ['confidence', 'Confidence', 'how firmly it belongs there'],
]

function confidenceLabel(value: number | null): string {
  if (value === null) return 'unclustered'
  if (value < 0.5) return 'between fields'
  return ''
}

export function ListView() {
  const nodes = useGraphStore((s) => s.nodes)
  const clusters = useGraphStore((s) => s.clusters)
  const vocabulary = useGraphStore((s) => s.tagVocabulary)
  const activeTags = useGraphStore((s) => s.activeTags)
  const toggleTag = useGraphStore((s) => s.toggleTag)
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const setSelected = useGraphStore((s) => s.setSelected)
  const setView = useGraphStore((s) => s.setView)
  const sortKey = useGraphStore((s) => s.sortKey)
  const sortAscending = useGraphStore((s) => s.sortAscending)
  const setSort = useGraphStore((s) => s.setSort)
  const labMode = useGraphStore((s) => s.labMode)

  const visible = useVisibleSet()
  const columns = labMode ? [...COLUMNS, ...LAB_COLUMNS] : COLUMNS

  const rows = useMemo(
    () => sortRows(buildRows(nodes, clusters, visible), sortKey, sortAscending),
    [nodes, clusters, visible, sortKey, sortAscending],
  )

  const openPdf = async (id: number, event: React.MouseEvent) => {
    // Without this the row's own click fires too and the sidebar opens behind
    // the viewer, which reads as the button doing two things.
    event.stopPropagation()
    const res = await fetch(`/api/papers/${id}/open`, { method: 'POST' })
    if (!res.ok) alert(`Could not open the PDF (${res.status})`)
  }

  const inspectOnMap = (index: number, event: React.MouseEvent) => {
    event.stopPropagation()
    // Selecting first means the map flies to this paper on arrival rather
    // than dropping the reader at wherever the camera happened to be.
    setSelected(index)
    setView('map')
  }

  if (rows.length === 0) {
    return (
      <div className="list-view empty">
        <p className="dim">No papers match the current search or filters.</p>
      </div>
    )
  }

  return (
    <div className="list-view">
      <table>
        <thead>
          <tr>
            {columns.map(([key, label, hint]) => (
              <th
                key={key}
                onClick={() => setSort(key)}
                title={`Sort by ${hint}`}
                className={sortKey === key ? 'sorted' : ''}
              >
                {label}
                {sortKey === key && (
                  <span className="arrow">{sortAscending ? '▲' : '▼'}</span>
                )}
              </th>
            ))}
            <th className="tags-column">Tags</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map(({ index, node, clusterName }) => (
            <tr
              key={node.id}
              className={index === selectedIndex ? 'selected' : ''}
              onClick={() => setSelected(index)}
            >
              <td className="title-cell">
                {node.title ?? <span className="dim">(untitled)</span>}
                {node.provisional && (
                  <span className="chip warn" title="Placed without a full refit">
                    provisional
                  </span>
                )}
              </td>
              <td className="numeric">{node.year ?? <span className="dim">—</span>}</td>
              <td>{clusterName ?? <span className="dim">unclustered</span>}</td>
              {labMode && (
                <td className="numeric">
                  {node.confidence === null ? (
                    <span className="dim">—</span>
                  ) : (
                    <span className="confidence">
                      <span className="bar">
                        <span
                          className="fill"
                          style={{ width: `${Math.round(node.confidence * 100)}%` }}
                        />
                      </span>
                      {node.confidence.toFixed(2)}
                    </span>
                  )}
                  {confidenceLabel(node.confidence) && (
                    <span className="dim"> {confidenceLabel(node.confidence)}</span>
                  )}
                </td>
              )}
              <td className="tags-column">
                {node.tags.map((tagId) => (
                  <button
                    key={tagId}
                    className={activeTags.has(tagId) ? 'chip tag-chip active' : 'chip tag-chip'}
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleTag(tagId)
                    }}
                    title="Filter by this tag"
                  >
                    {vocabulary[tagId] ?? tagId}
                  </button>
                ))}
              </td>
              <td className="actions-cell">
                <button onClick={(e) => inspectOnMap(index, e)} title="Show on the map">
                  Inspect
                </button>
                <button onClick={(e) => openPdf(node.id, e)}>Open PDF</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
