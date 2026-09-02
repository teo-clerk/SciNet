/**
 * Watch two embedding models disagree.
 *
 * Picks the first alternate run in the morph control, then sweeps the slider
 * out and back. The chrome (labels, skeleton, bridges) steps aside during
 * the morph, so what moves is exactly the disagreement: nodes gliding
 * between the two models' opinions of the same library.
 *
 * Requires an alternate run (scripts/build_alt_projection.py --apply).
 */

// React listens for 'input' on controlled inputs; setting .value directly is
// swallowed unless it goes through the native setter (the verify_list idiom).
const setRange = (selector, value) =>
  `(() => {
    const el = document.querySelector(${JSON.stringify(selector)})
    if (!el) return 'missing'
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    set.call(el, ${value})
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })()`

const pickFirstRun = `(() => {
  const sel = document.querySelector('.morph select')
  if (!sel || sel.options.length < 2) return 'no alternate runs'
  const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set
  set.call(sel, sel.options[1].value)
  sel.dispatchEvent(new Event('change', { bubbles: true }))
})()`

const sweep = []
for (const t of [15, 30, 45, 60, 75, 90, 100, 100, 80, 55, 30, 10, 0]) {
  sweep.push({ evaluate: setRange('.morph input[type="range"]', t) })
  sweep.push({ sleep: 420 })
}

export default {
  name: 'morph',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { wait: '.morph select', timeout: 10_000 },
    { sleep: 1800 },
    { evaluate: pickFirstRun },
    { wait: '.morph input[type="range"]', timeout: 10_000 },
    { sleep: 1200 }, // the run's positions arrive
    ...sweep,
    { sleep: 800 },
  ],
}
