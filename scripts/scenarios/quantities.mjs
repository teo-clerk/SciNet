/**
 * Measured values as geometry: filter the map by a physical range.
 *
 * Picks the frequency kind — on this corpus that means neural oscillations
 * and stimulation protocols — then narrows to 1–100 Hz and orbits the dimmed
 * map. A list row's "Show on the map" lands on a matching paper with its
 * Measured values on the card, and the Review tab closes the loop: the
 * adjudicator's doubt, resolved by a person with one click.
 *
 * The select is a controlled React element, so the scenario drives it with
 * the native value setter + a bubbled change event; insertText covers the
 * number inputs the same way the librarian scenario types its question.
 */

const pickKind = (kind) =>
  `(() => {
    const sel = document.querySelector('.quantity-filter select')
    const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set
    set.call(sel, '${kind}')
    sel.dispatchEvent(new Event('change', { bubbles: true }))
  })()`

const pressTab = (label) =>
  `[...document.querySelectorAll('.view-toggle button')]` +
  `.find((b) => b.textContent.trim().startsWith('${label}')).click()`

export default {
  name: 'quantities',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { wait: '.quantity-filter select', timeout: 10_000 },
    { sleep: 1800 }, // labels settle

    { evaluate: pickKind('frequency') },
    { sleep: 1800 }, // debounce + fetch; the map dims to papers with a frequency

    { click: 'input[aria-label="Minimum value"]' },
    { type: '1' },
    { click: 'input[aria-label="Maximum value"]' },
    { type: '100' },
    { sleep: 1800 }, // 1–100 Hz: the oscillation and stimulation literature

    { orbit: { x: 800, y: 450, steps: 45, dx: 7 } },
    { sleep: 1000 },

    { evaluate: pressTab('List') },
    { wait: '.list-view tbody tr', timeout: 10_000 },
    { sleep: 1500 }, // the same set, as rows
    {
      // A chosen subject, not the first row: the frequency-tagging paper
      // carries dozens of in-range rows, so its card actually shows the
      // feature — and its title is unique in the corpus, unlike the
      // voltage-imaging paper, which shares its exact title with its own
      // corrigendum. DOM clicks throughout — a synthetic mouse cannot
      // reach a button the panel has scrolled away.
      evaluate:
        "[...document.querySelectorAll('.list-view tbody tr')]" +
        ".find((tr) => tr.textContent.includes('Hierarchical Frequency Tagging'))" +
        "?.querySelector('button[title=\"Show on the map\"]')?.click()",
    },
    { wait: '.detail-panel', timeout: 10_000 },
    { sleep: 1800 }, // the card lands
    {
      evaluate:
        "[...document.querySelectorAll('.detail-panel h3')]" +
        ".find((h) => h.textContent === 'Measured values')" +
        "?.scrollIntoView({ behavior: 'smooth', block: 'start' })",
    },
    { sleep: 3000 }, // measured values on the card, sentences behind each row

    { evaluate: "document.querySelector('.detail-panel button.close')?.click()" },
    { evaluate: pressTab('Review') },
    { sleep: 2600 }, // what the adjudicator was unsure about
  ],
}
