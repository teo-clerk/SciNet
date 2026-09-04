/**
 * The reveal: the aerospace benchmark corpus settles into its regions.
 *
 * Nothing is clicked. The map loads, the region names fade in over radar
 * imaging, satellite-imagery learning, trajectory optimisation, astronomical
 * instrumentation and satellite positioning, and a slow half-orbit shows the
 * layout is three-dimensional and the regions are real volumes, not a flat
 * projection with labels. Recorded against the corpus fetch_demo_corpus.py
 * builds, so anyone can reproduce the frame.
 */
// A wheel notch on the canvas: the same event the orbit controls listen for.
// Eighty-five papers sit in four tight groups, and the default distance —
// right for a five-hundred-paper library — leaves them small in the frame.
const zoomIn = () =>
  `document.querySelector('canvas').dispatchEvent(` +
  `new WheelEvent('wheel', { deltaY: -120, bubbles: true, cancelable: true }))`

export default {
  name: 'aerospace_hero',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 2000 }, // region names fade in; bridges draw
    { evaluate: zoomIn() },
    { sleep: 150 },
    { evaluate: zoomIn() },
    { sleep: 150 },
    { evaluate: zoomIn() },
    { sleep: 150 },
    { evaluate: zoomIn() },
    { sleep: 1200 },
    { orbit: { x: 800, y: 460, steps: 120, dx: 5 } }, // a slow half-turn
    { sleep: 1800 },
  ],
}
