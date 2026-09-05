/**
 * One end of an idea trail: a paper, or a phrase.
 *
 * The two are different claims and the picker keeps them visibly apart. A
 * chosen paper is shown as its title with a way to let go of it; a phrase is
 * typed, and once the server has anchored it the picker says which paper it
 * landed on — "from Ethics" was the reader's words, and the trail actually
 * starts from a particular book.
 */
import type { TrailEnd } from '@/lib/trail'

interface Props {
  label: string
  end: TrailEnd
  onChange: (end: TrailEnd) => void
  /** Title of the paper the end names, when it names one. */
  chosenTitle: string | null
  /** Title of the paper a phrase was anchored to by the last request. */
  anchoredTitle: string | null
  canUseSelected: boolean
  onUseSelected: () => void
  disabled: boolean
}

export function EndpointPicker({
  label,
  end,
  onChange,
  chosenTitle,
  anchoredTitle,
  canUseSelected,
  onUseSelected,
  disabled,
}: Props) {
  return (
    <div className="endpoint">
      <span className="dim label">{label}</span>
      {end.paperId !== null ? (
        <span className="chosen">
          <span className="title">{chosenTitle ?? `#${end.paperId}`}</span>
          <button
            type="button"
            className="unchoose"
            aria-label={`Clear the ${label.toLowerCase()} paper`}
            onClick={() => onChange({ paperId: null, text: '' })}
            disabled={disabled}
          >
            ×
          </button>
        </span>
      ) : (
        <input
          value={end.text}
          aria-label={label}
          placeholder='a paper title, or an idea — "Ethics"'
          onChange={(event) => onChange({ paperId: null, text: event.target.value })}
          disabled={disabled}
        />
      )}
      <button
        type="button"
        className="use-selected"
        onClick={onUseSelected}
        disabled={disabled || !canUseSelected}
        title={canUseSelected ? 'Use the paper open in the side panel' : 'Select a paper on the map first'}
      >
        Use selected paper
      </button>
      {end.paperId === null && anchoredTitle && (
        <span className="dim anchored">→ anchored to {anchoredTitle}</span>
      )}
    </div>
  )
}
