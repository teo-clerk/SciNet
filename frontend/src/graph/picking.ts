/**
 * GPU picking.
 *
 * Each node's index is encoded as a colour and rendered into a 1x1 scissored
 * region under the cursor; reading that pixel back gives the node exactly.
 *
 * Chosen over CPU raycasting because raycasting against THREE.Points needs a
 * distance threshold that is wrong at some zoom level by construction, and
 * because the vertex shader scales points by depth and selection state — the
 * GPU already knows where each sprite ended up, and the CPU would have to
 * reimplement that maths to agree with it.
 */
import * as THREE from 'three'

import { BASE_POINT_SIZE } from './pointStyle'
import pickFragment from './shaders/pick.frag.glsl?raw'
import pickVertex from './shaders/pick.vert.glsl?raw'

/**
 * How far from the cursor a node may be and still be picked, in **CSS**
 * pixels — the unit the reader's hand works in.
 *
 * Device pixels were the wrong unit and the bug was invisible: a 15-device-
 * pixel window is 7.5 CSS pixels on a retina display, so the forgiveness the
 * constant claimed was halved by the hardware it ran on.
 *
 * Widening costs nothing in precision. ``nearestHit`` ranks by distance from
 * the cursor first, so a larger window only ever changes the answer where the
 * smaller one returned *nothing*.
 */
export const PICK_RADIUS_CSS = 12

/** Window width for a given device pixel ratio. Odd, so it has a true centre. */
export function pickWindowFor(pixelRatio: number): number {
  return Math.round(PICK_RADIUS_CSS * pixelRatio) * 2 + 1
}

/**
 * How much larger the pick disc is than the drawn one.
 *
 * The direct analogue of a raycaster's Points threshold. Generous: a click
 * that lands on the node beside the intended one is recoverable in a moment,
 * where a click that lands on nothing at all makes the map feel broken.
 */
export const PICK_INFLATE = 1.7

/**
 * Smallest pick disc, in device pixels, however far away the node is.
 *
 * Perspective shrinks a distant node toward a point, and no amount of
 * inflation rescues something multiplied by 300/-z at z = 300. This is the
 * floor that makes the far side of the map cost the same effort as the near
 * side. Overlap at extreme zoom-out is expected and is what the depth
 * tie-break below is for.
 */
export const MIN_PICK_SIZE_PX = 13

/**
 * The node under a pick window, or null if it hit nothing.
 *
 * Two rankings, in order.
 *
 * **Distance from the cursor, in whole-pixel rings.** Nearest rather than
 * first: scanning in row order would bias every ambiguous click toward
 * whichever node happened to be higher on screen. Rings rather than exact
 * distance so the second ranking gets to matter — two nodes a pixel apart are
 * both "under the cursor" as far as the hand is concerned.
 *
 * **Then depth: the node closest to the camera wins.** Within a ring the front
 * one is the one the reader believes they are pointing at, and it is the one
 * they can see. Depth testing already resolves overlap per pixel; this
 * resolves it across the several pixels a cursor actually covers.
 */
export function nearestHit(pixels: Uint8Array, window: number): number | null {
  const centre = (window - 1) / 2
  let best: number | null = null
  let bestRing = Infinity
  let bestDepth = Infinity

  for (let row = 0; row < window; row++) {
    for (let column = 0; column < window; column++) {
      const offset = (row * window + column) * 4
      const encoded =
        pixels[offset]! + (pixels[offset + 1]! << 8) + (pixels[offset + 2]! << 16)
      // 0 is the cleared background; indices are stored offset by one.
      if (encoded === 0) continue

      const dx = column - centre
      const dy = row - centre
      const ring = Math.round(Math.sqrt(dx * dx + dy * dy))
      const depth = pixels[offset + 3]!

      if (ring > bestRing) continue
      if (ring === bestRing && depth >= bestDepth) continue
      bestRing = ring
      bestDepth = depth
      best = encoded - 1
    }
  }
  return best
}


export class GpuPicker {
  private target: THREE.WebGLRenderTarget
  private scene: THREE.Scene
  private points: THREE.Points | null = null
  private material: THREE.ShaderMaterial
  /** Sized for the current pixel ratio; both are rebuilt when it changes. */
  private window = 0
  private buffer = new Uint8Array(0)

  constructor() {
    this.target = new THREE.WebGLRenderTarget(1, 1, {
      minFilter: THREE.NearestFilter,
      magFilter: THREE.NearestFilter,
      format: THREE.RGBAFormat,
      type: THREE.UnsignedByteType,
    })
    this.scene = new THREE.Scene()
    this.material = new THREE.ShaderMaterial({
      vertexShader: pickVertex,
      fragmentShader: pickFragment,
      uniforms: {
        uPixelRatio: { value: 1 },
        uBaseSize: { value: BASE_POINT_SIZE },
        uInflate: { value: PICK_INFLATE },
        uMinPickSize: { value: MIN_PICK_SIZE_PX },
      },
      transparent: false,
      depthWrite: true,
      depthTest: true,
    })
  }

  /** Point the picker at the live geometry; it shares, never copies. */
  attach(geometry: THREE.BufferGeometry): void {
    if (this.points) this.scene.remove(this.points)
    this.points = new THREE.Points(geometry, this.material)
    this.points.frustumCulled = false
    this.scene.add(this.points)
  }

  setPointScale(baseSize: number, pixelRatio: number): void {
    this.material.uniforms.uBaseSize!.value = baseSize
    this.material.uniforms.uPixelRatio!.value = pixelRatio
  }

  /** Grow the render target and readback buffer to match the pixel ratio. */
  private resizeFor(pixelRatio: number): number {
    const window = pickWindowFor(pixelRatio)
    if (window !== this.window) {
      this.window = window
      this.target.setSize(window, window)
      this.buffer = new Uint8Array(window * window * 4)
    }
    return window
  }

  /**
   * Node index under (x, y) in CSS pixels, or null.
   *
   * The camera is offset so that only the single pixel under the cursor is
   * rasterised — the cost is one tiny draw, not a full-frame pick pass.
   */
  pick(
    renderer: THREE.WebGLRenderer,
    camera: THREE.PerspectiveCamera,
    x: number,
    y: number,
    width: number,
    height: number,
  ): number | null {
    if (!this.points) return null

    // A window rather than a single pixel. Reading one pixel means the cursor
    // has to land inside a node's drawn disc exactly, which at typical zoom is
    // a few pixels across — accurate, and miserable to use. Rendering a small
    // neighbourhood and taking the nearest hit gives a forgiving target
    // without enlarging the nodes themselves or distorting what is on screen.
    const dpr = renderer.getPixelRatio()
    const window = this.resizeFor(dpr)
    const half = (window - 1) / 2
    const pickCamera = camera.clone()
    pickCamera.setViewOffset(
      width * dpr, height * dpr,
      Math.floor(x * dpr) - half, Math.floor(y * dpr) - half,
      window, window,
    )

    const previousTarget = renderer.getRenderTarget()
    const previousClear = renderer.getClearColor(new THREE.Color())
    const previousAlpha = renderer.getClearAlpha()

    renderer.setRenderTarget(this.target)
    renderer.setClearColor(0x000000, 1)
    renderer.clear()
    renderer.render(this.scene, pickCamera)
    renderer.readRenderTargetPixels(
      this.target, 0, 0, window, window, this.buffer,
    )

    renderer.setRenderTarget(previousTarget)
    renderer.setClearColor(previousClear, previousAlpha)
    pickCamera.clearViewOffset()

    return nearestHit(this.buffer, window)
  }

  dispose(): void {
    this.target.dispose()
    this.material.dispose()
  }
}
