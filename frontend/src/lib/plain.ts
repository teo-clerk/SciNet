/**
 * Plain-language forms of the pipeline's numbers.
 *
 * A cluster confidence of 0.37 and a manifold drift of 1.8 are the right thing
 * to show someone tuning the projection and the wrong thing to show someone
 * looking for a book. The numbers stay available behind the Lab toggle; these
 * are the sentences that stand in for them the rest of the time. Each one says
 * only what the number supports — "sits between" for a low confidence, nothing
 * at all when there is nothing to say.
 */

/** Below this a paper is nearer the edge of its region than its core. */
const LOW_CONFIDENCE = 0.5
/** Above this a paper is far from anything the map was fitted on; ~1 is typical. */
const HIGH_DRIFT = 1.6

export function placementSentence(
  region: string | null,
  confidence: number | null,
  drift: number | null,
): string | null {
  const parts = [
    region === null
      ? null
      : confidence === null
        ? `Sits in ${region}`
        : confidence < LOW_CONFIDENCE
          ? `Sits between ${region} and its neighbours`
          : `Sits firmly in ${region}`,
    drift !== null && drift > HIGH_DRIFT ? 'Unlike most of the library' : null,
  ].filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join(' · ') : null
}

export function pagesLabel(n: number | null): string | null {
  if (n === null || n < 1) return null
  return n === 1 ? '1 page' : `${n} pages`
}

const GENRE_LABELS: Record<string, string> = {
  'empirical-study': 'Empirical study',
  'theoretical-argument': 'Theoretical argument',
  'survey-or-review': 'Survey',
  'essay-or-commentary': 'Essay',
  'historical-narrative': 'History',
  'technical-report': 'Technical report',
  'literary-or-fiction': 'Literary work',
  'reference-or-textbook': 'Reference',
}

/** `other` and anything unrecognised read as nothing: a chip saying "Other" is
 *  a chip that tells the reader the classifier gave up, which is not worth a
 *  chip. */
export function genreLabel(genre: string | null): string | null {
  if (genre === null) return null
  return GENRE_LABELS[genre] ?? null
}

export const KIND_LABELS: Record<string, string> = {
  person: 'People',
  work: 'Works',
  concept: 'Ideas',
  event: 'Events',
  place: 'Places',
  organisation: 'Organisations',
}

/** People first, then the works they wrote, then the ideas in them: the order a
 *  reader would introduce a book in, not the order the extractor found them. */
const KIND_ORDER = ['person', 'work', 'concept', 'event', 'place', 'organisation']

export function entityGroups(
  entities: Array<{ name: string; kind: string }>,
): Array<[kind: string, names: string[]]> {
  return KIND_ORDER.map(
    (kind): [string, string[]] => [
      kind,
      entities.filter((e) => e.kind === kind).map((e) => e.name),
    ],
  ).filter(([, names]) => names.length > 0)
}
