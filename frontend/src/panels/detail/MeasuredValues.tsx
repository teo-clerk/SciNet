/**
 * The quantities the extractor found in the text.
 *
 * The heading is driven by a demo scenario (`.detail-panel h3` reading
 * "Measured values"), so it keeps its wording. The empty case is spoken only
 * in the lab: a reader does not need to be told a poem has no measurements,
 * but someone tuning the extractor needs to know it found nothing here.
 */
import type { QuantityRow } from '@/api/quantities'
import { formatQuantity } from '@/lib/quantities'

/** Enough to show what the work measures; the review view lists them all. */
const MAX_SHOWN = 8

export function MeasuredValues({
  quantities,
  labMode,
}: {
  quantities: QuantityRow[]
  labMode: boolean
}) {
  if (quantities.length === 0) {
    return labMode ? <p className="dim">No measured values found in this work.</p> : null
  }

  return (
    <section>
      <h3>Measured values</h3>
      {/* The sentence is the provenance; the tooltip carries it whole. */}
      <dl className="placement quantities">
        {quantities.slice(0, MAX_SHOWN).map((q) => (
          <div key={q.id} title={q.context_sentence}>
            <dt>
              {q.quantity_kind}
              {q.status !== 'auto' && q.status !== 'confirmed' && (
                <span className="dim"> ({q.status.replace('_', ' ')})</span>
              )}
            </dt>
            <dd>
              {formatQuantity(q.value_si, q.unit_si, q.quantity_kind)}
              <span className="dim"> · “{q.value_original} {q.unit_original}”</span>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
