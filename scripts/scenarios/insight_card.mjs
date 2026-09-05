/**
 * A work in plain words, before its abstract.
 *
 * Opens one paper from the List view — the only deterministic way to select
 * a node without the GPU picker — and holds on "The idea, in plain words":
 * the question, the argument, why it matters, then the claims and the people
 * the text turns on. The academic abstract is folded when a reading exists;
 * the last beat unfolds it, so the contrast is the point of the shot.
 */
const pressTab = (label) =>
  `[...document.querySelectorAll('.view-toggle button')]` +
  `.find((b) => b.textContent.trim().startsWith('${label}')).click()`

const openRow = (title) =>
  `[...document.querySelectorAll('.list-view tbody tr')]` +
  `.find((tr) => tr.textContent.includes(${JSON.stringify(title)}))` +
  `?.querySelector('button[title="Show on the map"]')?.click()`

const scrollTo = (heading) =>
  `[...document.querySelectorAll('.detail-panel h3')]` +
  `.find((h) => h.textContent === ${JSON.stringify(heading)})` +
  `?.scrollIntoView({ behavior: 'smooth', block: 'start' })`

export default {
  name: 'insight-card',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 1500 },
    { evaluate: pressTab('List') },
    { wait: '.list-view tbody tr', timeout: 10_000 },
    { sleep: 1200 },
    { evaluate: openRow('Active Inference: A Process Theory') },
    { wait: '.detail-panel .core-idea', timeout: 10_000 },
    { sleep: 3400 }, // the idea, in plain words
    { evaluate: scrollTo('Key claims') },
    { sleep: 2800 }, // the claims, and the people and works named
    { evaluate: scrollTo('The idea, in plain words') },
    { sleep: 600 },
    { click: '.detail-panel details.academic summary' },
    { sleep: 2400 }, // the abstract, for comparison
  ],
}
