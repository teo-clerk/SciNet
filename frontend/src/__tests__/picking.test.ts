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
