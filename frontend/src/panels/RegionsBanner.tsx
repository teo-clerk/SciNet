/**
 * A map with no regions, explained.
 *
 * The clusterer will not name anything below a floor of works, and a small
 * library that has just been placed looks broken without being told so: dots,
 * no names. One line, dismissable, gone by itself once regions appear.
 */
import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { needsMoreForRegions } from '@/lib/welcome'
import { useGraphStore } from '@/state/graphStore'

export function RegionsBanner() {
  const clusters = useGraphStore((s) => s.clusters)
  const count = useGraphStore((s) => s.count)
  const [minPapers, setMinPapers] = useState<number | null>(null)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    let alive = true
    api
      .system()
      .then((info) => alive && setMinPapers(info.regions_min_papers ?? null))
      // No floor known, no banner: guessing a number here would be worse.
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  if (dismissed || !needsMoreForRegions(clusters.length, count, minPapers)) return null

  return (
    <div className="regions-banner" role="status">
      <span>
        Regions appear at about {minPapers} works — add more, or install a sample library.
      </span>
      <button className="close" aria-label="Dismiss" onClick={() => setDismissed(true)}>
        ×
      </button>
    </div>
  )
}
