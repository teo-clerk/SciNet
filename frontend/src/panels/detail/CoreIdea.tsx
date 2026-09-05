/**
 * The work in plain words, before the abstract.
 *
 * An abstract is written for the author's peers; these three lines are for
 * whoever else picked the book up. They come first because that is who the
 * panel is for by default — the abstract stays a click away, open by itself
 * only while there is nothing plainer to show.
 */
import type { PaperDetail } from '@/api/graph'
import { genreLabel, ideaLines, missingInsightNote } from '@/lib/plain'

export function CoreIdea({ paper }: { paper: PaperDetail }) {
  const insight = paper.insight

  if (!insight) {
    return (
      <section>
        <h3>The idea, in plain words</h3>
        <p className="dim">{missingInsightNote(paper.status)}</p>
      </section>
    )
  }

  const lines = ideaLines(insight)
  const genre = genreLabel(insight.genre)

  return (
    <section>
      <h3>The idea, in plain words</h3>
      {genre && (
        <div className="meta-row">
          <span className="chip">{genre}</span>
        </div>
      )}
      {lines.length > 0 && (
        <dl className="core-idea prose">
          {lines.map(([label, text]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{text}</dd>
            </div>
          ))}
        </dl>
      )}
      {!insight.grounded && (
        /* The model's own verdict on itself. Shown, because a confident
           sentence with thin evidence behind it reads exactly like one with
           good evidence, and the reader has no other way to tell. */
        <p className="assembled">Not fully grounded in the text — read it as a guess.</p>
      )}
    </section>
  )
}
