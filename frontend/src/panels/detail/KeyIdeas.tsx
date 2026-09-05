/**
 * What the work claims, and who and what it names.
 *
 * Below the measured values, because a claim is the sentence a number was
 * measured in support of. The entities are grouped the way a reader would
 * introduce a book — people, then their works, then the ideas in them — rather
 * than in the order the extractor met them.
 */
import type { PaperInsight } from '@/api/graph'
import { entityGroups, KIND_LABELS } from '@/lib/plain'

export function KeyIdeas({ insight }: { insight: PaperInsight }) {
  const groups = entityGroups(insight.entities)

  return (
    <>
      {insight.claims.length > 0 && (
        <section>
          <h3>Key claims</h3>
          <ul className="claims prose">
            {insight.claims.map((claim, i) => (
              <li key={i}>{claim}</li>
            ))}
          </ul>
        </section>
      )}

      {groups.length > 0 && (
        <section>
          <h3>Named in this work</h3>
          {groups.map(([kind, names]) => (
            <div key={kind} className="entity-group">
              <span className="dim">{KIND_LABELS[kind]}</span>
              {names.map((name) => (
                <span key={name} className="chip">
                  {name}
                </span>
              ))}
            </div>
          ))}
        </section>
      )}
    </>
  )
}
