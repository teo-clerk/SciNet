/**
 * Search, tag filters and colour mode.
 *
 * Filtering dims rather than removes: a filtered map that deletes its context
 * tells you what matched but not where it sits, which is the whole point of
 * having a map.
 */
import { useGraphStore, type ColorMode } from '@/state/graphStore'
import { useVisibleSet } from '@/lib/filtering'

const MODES: Array<[ColorMode, string]> = [
  ['cluster', 'Cluster'],
  ['year', 'Year'],
  ['provisional', 'Provisional'],
]

export function FilterBar() {
  const query = useGraphStore((s) => s.query)
  const setQuery = useGraphStore((s) => s.setQuery)
  const colorMode = useGraphStore((s) => s.colorMode)
  const setColorMode = useGraphStore((s) => s.setColorMode)
  const vocabulary = useGraphStore((s) => s.tagVocabulary)
  const activeTags = useGraphStore((s) => s.activeTags)
  const toggleTag = useGraphStore((s) => s.toggleTag)
  const clearFilters = useGraphStore((s) => s.clearFilters)
  const count = useGraphStore((s) => s.count)

  const visible = useVisibleSet()
  const shown = visible === null ? count : visible.size
  const filtering = visible !== null

  return (
    <div className="filter-bar">
      <input
        className="search"
        type="search"
        placeholder="Search titles…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="modes">
        {MODES.map(([mode, label]) => (
          <button
            key={mode}
            className={colorMode === mode ? 'active' : ''}
            onClick={() => setColorMode(mode)}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="tags">
        {vocabulary.map((slug, id) => (
          <button
            key={slug}
            className={activeTags.has(id) ? 'tag active' : 'tag'}
            onClick={() => toggleTag(id)}
          >
            {slug}
          </button>
        ))}
      </div>

      <span className="spacer" />
      <span className={filtering ? 'count active' : 'count'}>
        {shown.toLocaleString()} / {count.toLocaleString()}
      </span>
      {filtering && (
        <button className="clear" onClick={clearFilters}>
          clear
        </button>
      )}
    </div>
  )
}
