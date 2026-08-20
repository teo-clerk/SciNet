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
 * Width of the neighbourhood sampled under the cursor, in device pixels.
 *
 * Odd so it has a true centre. Nine gives roughly a four-pixel margin in every
 * direction, which is forgiving enough for a small node without letting a
 * click reach past a neighbouring one.
 */
export const PICK_WINDOW = 9

/**
 * The node nearest the centre of a pick window, or null if it hit nothing.
 *
 * Nearest rather than first, so that when the cursor sits between two nodes
 * the one it is actually closest to wins — scanning in row order would bias
 * every ambiguous click toward whichever happened to be higher on screen.
 */
export function nearestHit(pixels: Uint8Array, window = PICK_WINDOW): number | null {
  const centre = (window - 1) / 2
  let best: number | null = null
  let bestDistance = Infinity

  for (let row = 0; row < window; row++) {
    for (let column = 0; column < window; column++) {
      const offset = (row * window + column) * 4
      const encoded =
        pixels[offset]! + (pixels[offset + 1]! << 8) + (pixels[offset + 2]! << 16)
      // 0 is the cleared background; indices are stored offset by one.
      if (encoded === 0) continue

      const dx = column - centre
      const dy = row - centre
      const distance = dx * dx + dy * dy
      if (distance < bestDistance) {
        bestDistance = distance
        best = encoded - 1
      }
    }
  }
  return best
}


export class GpuPicker {
  private target: THREE.WebGLRenderTarget
  private scene: THREE.Scene
  private points: THREE.Points | null = null
  private material: THREE.ShaderMaterial
  private buffer = new Uint8Array(PICK_WINDOW * PICK_WINDOW * 4)

  constructor() {
    this.target = new THREE.WebGLRenderTarget(PICK_WINDOW, PICK_WINDOW, {
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
    const half = (PICK_WINDOW - 1) / 2
    const pickCamera = camera.clone()
    pickCamera.setViewOffset(
      width * dpr, height * dpr,
      Math.floor(x * dpr) - half, Math.floor(y * dpr) - half,
      PICK_WINDOW, PICK_WINDOW,
    )

    const previousTarget = renderer.getRenderTarget()
    const previousClear = renderer.getClearColor(new THREE.Color())
    const previousAlpha = renderer.getClearAlpha()

    renderer.setRenderTarget(this.target)
    renderer.setClearColor(0x000000, 1)
    renderer.clear()
    renderer.render(this.scene, pickCamera)
    renderer.readRenderTargetPixels(
      this.target, 0, 0, PICK_WINDOW, PICK_WINDOW, this.buffer,
    )

    renderer.setRenderTarget(previousTarget)
    renderer.setClearColor(previousClear, previousAlpha)
    pickCamera.clearViewOffset()

    return nearestHit(this.buffer)
  }

  dispose(): void {
    this.target.dispose()
    this.material.dispose()
  }
}
