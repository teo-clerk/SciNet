/**
 * A question that lives between two fields.
 *
 * "Ionospheric delay correction for interferometry" is a GNSS problem that
 * the InSAR literature has to solve, so its nearest papers should light up
 * between the positioning and the radar regions — the one image that
 * explains what an embedding space is to someone who has never seen one.
 * Meaning mode is selected explicitly, the query is typed, and the cut waits
 * for the spinner to clear rather than guessing how long the encoder takes.
 */
const pressMode = (label) =>
  `[...document.querySelectorAll('.search-modes button')]` +
  `.find((b) => b.textContent.trim() === '${label}').click()`

// A wheel notch on the canvas, as in the hero scenario: 85 papers sit in
// four tight groups and the default distance leaves them small.
const zoomIn = () =>
  `document.querySelector('canvas').dispatchEvent(` +
  `new WheelEvent('wheel', { deltaY: -120, bubbles: true, cancelable: true }))`

export default {
  name: 'aerospace_search',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 1500 },
    { evaluate: zoomIn() },
    { sleep: 150 },
    { evaluate: zoomIn() },
    { sleep: 150 },
    { evaluate: zoomIn() },
    { sleep: 150 },
    { evaluate: zoomIn() },
    { sleep: 1500 }, // the map settles first, so the change reads as a change
    { evaluate: pressMode('Meaning') },
    { click: '.search-box input' },
    { type: 'ionospheric delay correction for interferometry' },
    { sleep: 700 }, // debounce; the spinner appears
    { waitGone: '.search-box .spinner', timeout: 90_000 }, // results landed
    { sleep: 2200 }, // the hits, lit, between GNSS and SAR
    { orbit: { x: 800, y: 460, steps: 70, dx: 6 } },
    { sleep: 1800 },
  ],
}
