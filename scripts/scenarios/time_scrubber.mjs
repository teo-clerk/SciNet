/**
 * The corpus's history, replayed: press play, watch fields emerge.
 *
 * The whole demo is one click — the scrubber's play button — followed by the
 * eight seconds the play loop takes to walk the timeline. Papers beyond the
 * cutoff ghost to the filtered look, so regions fade up as their years
 * arrive instead of popping in.
 */
export default {
  name: 'time-scrubber',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { wait: '.scrubber button', timeout: 10_000 },
    { sleep: 1800 }, // labels settle before the story starts
    { click: '.scrubber button' },
    { sleep: 8800 }, // the play loop's full pass
    { sleep: 600 }, // the release — filter lets go, full map returns
  ],
}
