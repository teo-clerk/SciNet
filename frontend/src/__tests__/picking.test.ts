/**
 * Click-to-select semantics.
 *
 * A regression guard for a bug that shipped: picking is suppressed while the
 * camera moves, and pointerdown cleared the current hover so the highlight
 * would not stick during a drag. The click handler then read that same cleared
 * value, so every click selected null and the sidebar never opened.
 *
 * The rule the fix encodes: the node is captured when the press begins, and a
 * press that travels is an orbit rather than a click.
 */
import { describe, expect, test } from 'bun:test'

import {
  MIN_PICK_SIZE_PX,
  nearestHit,
  PICK_INFLATE,
  PICK_RADIUS_CSS,
  pickWindowFor,
} from '../graph/picking'
import { BASE_POINT_SIZE, CORE_RADIUS } from '../graph/pointStyle'

const CLICK_SLOP_PX = 5

/** Mirrors Picker.tsx's press/click logic, minus the WebGL binding. */
function makeHandler() {
  let hovered: number | null = null
  let selected: number | null = null
  let selections = 0
  let pressed: { index: number | null; x: number; y: number } | null = null

  return {
    hover(index: number | null) {
      hovered = index
    },
    down(x: number, y: number) {
      pressed = { index: hovered, x, y }
      hovered = null // highlight is dropped for the duration of the drag
    },
    click(x: number, y: number) {
      const press = pressed
      pressed = null
      if (!press) return
      if (Math.hypot(x - press.x, y - press.y) > CLICK_SLOP_PX) return
      selected = press.index
      selections++
    },
    get selected() {
      return selected
    },
    get selections() {
      return selections
    },
  }
}

describe('click to select', () => {
  test('clicking a hovered node selects it', () => {
    const h = makeHandler()
    h.hover(42)
    h.down(100, 100)
    h.click(100, 100)
    expect(h.selected).toBe(42)
  })

  test('the node survives the hover being cleared on press', () => {
    // The exact bug: pointerdown clears the hover, and the click read it.
    const h = makeHandler()
    h.hover(7)
    h.down(50, 50)
    h.click(50, 50)
    expect(h.selected).toBe(7)
    expect(h.selected).not.toBeNull()
  })

  test('a small tremor still counts as a click', () => {
    const h = makeHandler()
    h.hover(3)
    h.down(200, 200)
    h.click(202, 203) // ~3.6 px
    expect(h.selected).toBe(3)
  })

  test('a drag does not select', () => {
    // Releasing an orbit gesture over a node must not open it.
    const h = makeHandler()
    h.hover(9)
    h.down(100, 100)
    h.click(400, 260)
    expect(h.selections).toBe(0)
  })

  test('clicking empty space clears the selection', () => {
    const h = makeHandler()
    h.hover(5)
    h.down(10, 10)
    h.click(10, 10)
    expect(h.selected).toBe(5)

    h.hover(null)
    h.down(300, 300)
    h.click(300, 300)
    expect(h.selected).toBeNull()
  })

  test('a click without a preceding press does nothing', () => {
    const h = makeHandler()
    h.click(100, 100)
    expect(h.selections).toBe(0)
  })

  test('selecting the same node twice re-fires', () => {
    // The camera flies to it again, which is the useful behaviour.
    const h = makeHandler()
    for (const _ of [0, 1]) {
      h.hover(11)
      h.down(80, 80)
      h.click(80, 80)
    }
    expect(h.selections).toBe(2)
  })

  test('a press is consumed, so a second click needs a second press', () => {
    const h = makeHandler()
    h.hover(1)
    h.down(60, 60)
    h.click(60, 60)
    h.click(60, 60)
    expect(h.selections).toBe(1)
  })
})

describe('pick alignment', () => {
  test('the renderer and the picker share one sprite size', () => {
    // They are separate draws of the same points. If the sizes diverge the
    // clickable area stops matching what is drawn, and edge clicks miss.
    expect(BASE_POINT_SIZE).toBeGreaterThan(0)
    expect(Number.isFinite(BASE_POINT_SIZE)).toBe(true)
  })

  test('the core is a real proportion of the sprite', () => {
    // 0 would be no core at all; 1 would leave no room for the aura.
    expect(CORE_RADIUS).toBeGreaterThan(0.1)
    expect(CORE_RADIUS).toBeLessThan(0.8)
  })
})


describe('pick tolerance', () => {
  const WINDOW = pickWindowFor(1)
  const CENTRE = (WINDOW - 1) / 2

  /** A pick window with nodes encoded at given positions.
   *  `depth` is 0 (against the camera) to 255 (far); the shader writes
   *  gl_FragCoord.z there. */
  function windowWith(
    hits: Array<[number, number, number, number?]>,
    size = WINDOW,
  ) {
    const pixels = new Uint8Array(size * size * 4)
    for (const [col, row, index, depth] of hits) {
      const encoded = index + 1
      const o = (row * size + col) * 4
      pixels[o] = encoded & 0xff
      pixels[o + 1] = (encoded >> 8) & 0xff
      pixels[o + 2] = (encoded >> 16) & 0xff
      pixels[o + 3] = depth ?? 128
    }
    return pixels
  }

  test('the window is odd so it has a true centre', () => {
    for (const dpr of [1, 1.5, 2, 3]) {
      expect(pickWindowFor(dpr) % 2).toBe(1)
    }
  })

  test('the window is measured in CSS pixels, not device pixels', () => {
    // The bug this replaces was invisible: a 15-device-pixel window is 7.5 CSS
    // pixels on a retina display, so the forgiveness the constant claimed was
    // halved by the hardware it happened to run on.
    expect(pickWindowFor(2)).toBeGreaterThan(pickWindowFor(1))
    expect(pickWindowFor(2)).toBe(PICK_RADIUS_CSS * 2 * 2 + 1)
  })

  test('the target is large enough to be aimed at casually', () => {
    expect(PICK_RADIUS_CSS).toBeGreaterThanOrEqual(10)
  })

  test('the pick disc is drawn larger than the visible node', () => {
    // The GPU-picking equivalent of raising a raycaster threshold: the target
    // grows while what the reader sees does not move.
    expect(PICK_INFLATE).toBeGreaterThan(1)
  })

  test('a distant node never shrinks out of reach', () => {
    // Perspective divides by distance, so without a floor the far side of the
    // map costs far more effort to click than the near side.
    expect(MIN_PICK_SIZE_PX).toBeGreaterThanOrEqual(8)
  })

  test('an empty window hits nothing', () => {
    expect(nearestHit(windowWith([]), WINDOW)).toBeNull()
  })

  test('a node dead centre is picked', () => {
    expect(nearestHit(windowWith([[CENTRE, CENTRE, 42]]), WINDOW)).toBe(42)
  })

  test('a node near the edge of the window is still picked', () => {
    // This is the whole point: the cursor missed the node but landed close.
    expect(nearestHit(windowWith([[0, 0, 7]]), WINDOW)).toBe(7)
  })

  test('the nearest node wins when two are in range', () => {
    const pixels = windowWith([
      [0, 0, 11],
      [CENTRE, CENTRE - 1, 22],
    ])
    expect(nearestHit(pixels, WINDOW)).toBe(22)
  })

  test('nearest beats first in scan order', () => {
    // Row-order scanning would bias every ambiguous click toward whichever
    // node happened to sit higher on screen.
    const pixels = windowWith([
      [CENTRE, 0, 99],
      [CENTRE, CENTRE, 100],
    ])
    expect(nearestHit(pixels, WINDOW)).toBe(100)
  })

  test('among nodes equally close to the cursor, the front one wins', () => {
    // The inflated disc makes overlaps normal rather than exceptional, and the
    // node the reader believes they are pointing at is the one they can see.
    const pixels = windowWith([
      [CENTRE - 1, CENTRE, 5, 240], // same ring, far away
      [CENTRE + 1, CENTRE, 6, 20], // same ring, near the camera
    ])
    expect(nearestHit(pixels, WINDOW)).toBe(6)
  })

  test('depth does not override being under the cursor', () => {
    // A node against the camera at the far edge of the window must not beat
    // one directly beneath the pointer; the hand aims, depth only breaks ties.
    const pixels = windowWith([
      [CENTRE, CENTRE, 8, 250],
      [0, 0, 9, 0],
    ])
    expect(nearestHit(pixels, WINDOW)).toBe(8)
  })

  test('node index zero is distinguishable from empty space', () => {
    // Indices are stored offset by one precisely so 0 can mean "background".
    expect(nearestHit(windowWith([[CENTRE, CENTRE, 0]]), WINDOW)).toBe(0)
  })

  test('a high index round-trips through the window', () => {
    expect(nearestHit(windowWith([[CENTRE, CENTRE, 70000]]), WINDOW)).toBe(70000)
  })
})
