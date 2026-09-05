/**
 * Where the work sits on the map, and how surely.
 *
 * Two readings of the same two numbers: bars and decimals for whoever is
 * tuning the projection, and the same facts as a sentence for everyone else.
 */
import type { PaperDetail } from '@/api/graph'
import { placementSentence } from '@/lib/plain'

export function Placement({ paper, labMode }: { paper: PaperDetail; labMode: boolean }) {
  if (paper.cluster_confidence === null && paper.manifold_drift === null) return null

  const sentence = placementSentence(
    paper.cluster_name,
    paper.cluster_confidence,
    paper.manifold_drift,
  )

  return (
    <section>
      <h3>Placement</h3>
      <dl className="placement">
        {paper.cluster_name && (
          <>
            <dt>Region</dt>
            <dd>{paper.cluster_name}</dd>
          </>
        )}
        {labMode && paper.cluster_confidence !== null && (
          <>
            <dt title="How strongly this paper belongs to its cluster">Cluster confidence</dt>
            <dd>
              <span className="bar">
                <span
                  className="fill"
                  style={{ width: `${Math.round(paper.cluster_confidence * 100)}%` }}
                />
              </span>
              {paper.cluster_confidence.toFixed(2)}
              {paper.cluster_confidence < 0.5 && (
                <span className="dim"> · sits between fields</span>
              )}
            </dd>
          </>
        )}
        {labMode && paper.manifold_drift !== null && (
          <>
            <dt title="Distance from the region the map was fitted on; ~1 is typical">
              Embedding drift
            </dt>
            <dd>
              {paper.manifold_drift.toFixed(2)}
              {paper.manifold_drift > 1.6 && (
                <span className="warn"> · unlike anything else here</span>
              )}
            </dd>
          </>
        )}
      </dl>
      {!labMode && sentence && <p className="prose">{sentence}</p>}
    </section>
  )
}
