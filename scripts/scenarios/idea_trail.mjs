/**
 * An idea trail: from one phrase to another through the library's own
 * neighbour graph, then the tour.
 *
 * A phrase end has to be embedded, so the encoder is warmed off camera —
 * a Meaning search, its spinner gone, the box cleared — and recording starts
 * only then. Typing never presses Enter in this harness; the form's own
 * submit button is clicked. The two phrases were chosen for a chain whose
 * every stop carries a readable title; they cross from embodied cognition
 * through an unplaced book into nonlinear dynamics.
 */
const pressMode = (label) =>
  `[...document.querySelectorAll('.search-modes button')]` +
  `.find((b) => b.textContent.trim() === '${label}').click()`

const clearSearch = `(() => {
  const el = document.querySelector('.search-box input')
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
  set.call(el, '')
  el.dispatchEvent(new Event('input', { bubbles: true }))
})()`

const pressControl = (label) =>
  `[...document.querySelectorAll('.trail-panel .controls button')]` +
  `.find((b) => b.textContent.trim() === '${label}').click()`

export default {
  name: 'idea-trail',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    // -- warm the encoder, off camera --
    { evaluate: pressMode('Meaning') },
    { click: '.search-box input' },
    { type: 'embodied cognition' },
    { sleep: 700 },
    { waitGone: '.search-box .spinner', timeout: 90_000 },
    { evaluate: clearSearch },
    { evaluate: pressMode('Title') },
    { sleep: 1200 },
    // -- the shot --
    { startRecording: true },
    { sleep: 1200 },
    {
      evaluate:
        "[...document.querySelectorAll('.view-toggle button')]" +
        ".find((b) => b.textContent.includes('Trail')).click()",
    },
    { wait: '.trail-panel input[aria-label="Start"]', timeout: 10_000 },
    { sleep: 600 },
    { click: '.trail-panel input[aria-label="Start"]' },
    { type: 'embodied cognition' },
    { sleep: 500 },
    { click: '.trail-panel input[aria-label="Destination"]' },
    { type: 'nonlinear dynamics and chaos' },
    { sleep: 700 },
    { click: '.trail-panel .row button[type="submit"]' },
    { wait: '.trail-panel .stop', timeout: 30_000 },
    { wait: '.trail-marker', timeout: 10_000 },
    { sleep: 3200 }, // the stops, named; the line crawling on the map
    { evaluate: pressControl('Tour') },
    { sleep: 8400 }, // three stops at the tour's cadence
    { evaluate: pressControl('Stop') },
    { sleep: 1800 },
  ],
}
