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

import pickFragment from './shaders/pick.frag.glsl?raw'
import pickVertex from './shaders/pick.vert.glsl?raw'

export class GpuPicker {
  private target: THREE.WebGLRenderTarget
  private scene: THREE.Scene
  private points: THREE.Points | null = null
  private material: THREE.ShaderMaterial
  private buffer = new Uint8Array(4)

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
        uBaseSize: { value: 3.4 },
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

    const dpr = renderer.getPixelRatio()
    const pickCamera = camera.clone()
    pickCamera.setViewOffset(
      width * dpr, height * dpr,
      Math.floor(x * dpr), Math.floor(y * dpr),
      1, 1,
    )

    const previousTarget = renderer.getRenderTarget()
    const previousClear = renderer.getClearColor(new THREE.Color())
    const previousAlpha = renderer.getClearAlpha()

    renderer.setRenderTarget(this.target)
    renderer.setClearColor(0x000000, 1)
    renderer.clear()
    renderer.render(this.scene, pickCamera)
    renderer.readRenderTargetPixels(this.target, 0, 0, 1, 1, this.buffer)

    renderer.setRenderTarget(previousTarget)
    renderer.setClearColor(previousClear, previousAlpha)
    pickCamera.clearViewOffset()

    const [r, g, b] = this.buffer
    const encoded = r! + (g! << 8) + (b! << 16)
    // 0 is the cleared background; indices are stored offset by one.
    return encoded === 0 ? null : encoded - 1
  }

  dispose(): void {
    this.target.dispose()
    this.material.dispose()
  }
}
