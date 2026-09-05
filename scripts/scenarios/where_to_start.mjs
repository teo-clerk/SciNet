/**
 * Where to start, and why — then the reading order drawn on the map.
 *
 * A title search narrows the map to one topic (instant, no spinner: it runs
 * in the browser), which is what makes the filter bar offer "Where do I
 * start?". The card names the entry point with its reasons as chips; the
 * Reading order button lays a numbered trail across the map and frames it.
 */
const pressMode = (label) =>
  `[...document.querySelectorAll('.search-modes button')]` +
  `.find((b) => b.textContent.trim() === '${label}').click()`

export default {
  name: 'where-to-start',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 1500 },
    { evaluate: pressMode('Title') },
    { click: '.search-box input' },
    { type: 'active inference' },
    { sleep: 1400 }, // the map dims to the topic
    { wait: '.filter-bar button.start', timeout: 10_000 },
    { click: '.filter-bar button.start' },
    { wait: '.entry-card .entry-point', timeout: 15_000 },
    { sleep: 3200 }, // the entry point, and the reasons
    { click: '.entry-card button.reading-order' },
    { wait: '.trail-marker', timeout: 10_000 },
    { sleep: 4600 }, // the numbered order across the map
  ],
}
