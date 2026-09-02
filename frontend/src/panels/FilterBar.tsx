/**
 * Search, tag filters and colour mode.
 *
 * Filtering dims rather than removes: a filtered map that deletes its context
 * tells you what matched but not where it sits, which is the whole point of
 * having a map.
 */
import { MapControls } from '@/panels/MapControls'
import { MorphSlider } from '@/panels/MorphSlider'
import { SearchBox } from '@/panels/SearchBox'
import { TimeScrubber } from '@/panels/TimeScrubber'
import { UploadButton } from '@/panels/UploadButton'
import { useGraphStore, type ColorMode } from '@/state/graphStore'
import { useVisibleSet } from '@/lib/filtering'
import { useQuarantineCount } from '@/lib/useQuarantineCount'

const MODES: Array<[ColorMode, string]> = [
  ['cluster', 'Cluster'],
  ['year', 'Year'],
  ['provisional', 'Provisional'],
]

export function FilterBar() {
  const colorMode = useGraphStore((s) => s.colorMode)
  const setColorMode = useGraphStore((s) => s.setColorMode)
  const vocabulary = useGraphStore((s) => s.tagVocabulary)
  const activeTags = useGraphStore((s) => s.activeTags)
  const toggleTag = useGraphStore((s) => s.toggleTag)
  const clearFilters = useGraphStore((s) => s.clearFilters)
  const count = useGraphStore((s) => s.count)
  const view = useGraphStore((s) => s.view)
  const setView = useGraphStore((s) => s.setView)
  const quarantined = useQuarantineCount()

  const visible = useVisibleSet()
  const shown = visible === null ? count : visible.size
  const filtering = visible !== null

  return (
    <div className="filter-bar">
      <SearchBox />

      <div className="view-toggle">
        <button
          className={view === 'map' ? 'active' : ''}
          onClick={() => setView('map')}
        >
          3D Map
        </button>
        <button
          className={view === 'list' ? 'active' : ''}
          onClick={() => setView('list')}
        >
          List
        </button>
        {/* Only shown once something is in it. An always-visible tab reading
            "Quarantine 0" trains the reader to ignore it, which is the one
            thing it must not do on the day it says 12. */}
        {quarantined > 0 && (
          <button
            className={view === 'quarantine' ? 'active warn' : 'warn'}
            onClick={() => setView('quarantine')}
            title="Files the pipeline could not read"
          >
            Quarantine <span className="badge count">{quarantined}</span>
          </button>
        )}
        <button
          className={view === 'models' ? 'active' : ''}
          onClick={() => setView('models')}
          title="Hardware, the model catalog, and task routing"
        >
          Models
        </button>
      </div>

      {view === 'map' && <MapControls />}

      {view === 'map' && (
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
      )}

      {view === 'map' && <TimeScrubber />}

      {view === 'map' && <MorphSlider />}

      {/* The Model Lab is not a view over papers: a tag cloud and an
          N / M counter above it would describe a corpus it does not show. */}
      {view !== 'models' && (
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
      )}

      <span className="spacer" />
      {view !== 'models' && (
        <>
          <UploadButton />
          <span className={filtering ? 'count active' : 'count'}>
            {shown.toLocaleString()} / {count.toLocaleString()}
          </span>
          {filtering && (
            <button className="clear" onClick={clearFilters}>
              clear
            </button>
          )}
        </>
      )}
    </div>
  )
}
