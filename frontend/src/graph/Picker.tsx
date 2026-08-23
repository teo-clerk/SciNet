/**
 * Wires GPU picking to the pointer, and drives hover/selection.
 *
 * Throttled to ~30 Hz: a readback stalls the pipeline briefly, so doing it on
 * every pointermove would cost more than the pick is worth. 33 ms is well
 * under the threshold where hover feels laggy.
 */
import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'

import { GpuPicker } from './picking'
import { BASE_POINT_SIZE } from './pointStyle'
import { useGraphStore } from '@/state/graphStore'

const PICK_INTERVAL_MS = 33
/**
 * How long the camera must be still before picking resumes.
 *
 * readRenderTargetPixels is a synchronous GPU->CPU readback: it stalls the
 * pipeline until the frame it needs has finished. That is cheap in isolation
 * and ruinous every other frame — measured on the real 293-node corpus,
 * picking during an orbit drag cost p95 25.7 ms and 50 fps, against 17.3 ms
 * and 60 fps with it suppressed.
 *
 * Suppressing it while the camera moves is not a workaround but the correct
 * behaviour: a pointer dragging the scene is grabbing it, not pointing at
 * something, and no hover the user cares about happens mid-orbit.
 */
const SETTLE_MS = 90
/** How far a press may travel and still count as a click rather than a drag. */
const CLICK_SLOP_PX = 5

export function Picker() {
  const { gl, camera, scene, size } = useThree()
  const setHovered = useGraphStore((s) => s.setHovered)
  const setSelected = useGraphStore((s) => s.setSelected)
  const buffers = useGraphStore((s) => s.buffers)

  const picker = useMemo(() => new GpuPicker(), [])
  const pointer = useRef<{ x: number; y: number } | null>(null)
  const lastPick = useRef(0)
  const hovered = useRef<number | null>(null)
  const dragging = useRef(false)
  /** What was under the cursor when the press began, and where it began. */
  const pressed = useRef<{ index: number | null; x: number; y: number } | null>(null)
  const lastCameraMove = useRef(0)
  const lastCameraPos = useRef(new THREE.Vector3())

  useEffect(() => () => picker.dispose(), [picker])

  // Share the live geometry rather than duplicating it, so picking always
  // reflects the positions actually on screen.
  useEffect(() => {
    const points = scene.children.find(
      (child): child is THREE.Points => (child as THREE.Points).isPoints === true,
    )
    if (points) picker.attach(points.geometry as THREE.BufferGeometry)
  }, [picker, scene, buffers])

  useEffect(() => {
    const canvas = gl.domElement

    const onMove = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      pointer.current = { x: event.clientX - rect.left, y: event.clientY - rect.top }
    }
    const onLeave = () => {
      pointer.current = null
      hovered.current = null
      dragging.current = false
      setHovered(null)
      canvas.style.cursor = ''
    }
    const onDown = (event: PointerEvent) => {
      dragging.current = true
      // Remember both what was under the cursor and where the press started.
      // The node has to be captured here because picking is suppressed while
      // the camera moves, so by the time the click fires there may be no
      // current hover to read — an earlier version cleared it here and then
      // selected that same cleared value, so every click deselected.
      pressed.current = {
        index: hovered.current,
        x: event.clientX,
        y: event.clientY,
      }
      if (hovered.current !== null) {
        // Drop the highlight for the duration of the drag; the remembered
        // index above is what the click will use.
        hovered.current = null
        setHovered(null)
      }
    }
    const onUp = () => {
      dragging.current = false
    }
    const onClick = (event: MouseEvent) => {
      const press = pressed.current
      pressed.current = null
      if (!press) return

      // A press that travelled is an orbit, not a click on a paper. Without
      // this, releasing a rotate gesture over a node would open it.
      const travelled = Math.hypot(
        event.clientX - press.x,
        event.clientY - press.y,
      )
      if (travelled > CLICK_SLOP_PX) return

      // Selecting null is meaningful: clicking empty space closes the panel.
      setSelected(press.index)
    }

    canvas.addEventListener('pointermove', onMove)
    canvas.addEventListener('pointerleave', onLeave)
    canvas.addEventListener('pointerdown', onDown)
    window.addEventListener('pointerup', onUp)
    canvas.addEventListener('click', onClick)
    return () => {
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerleave', onLeave)
      canvas.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointerup', onUp)
      canvas.removeEventListener('click', onClick)
      canvas.style.cursor = ''
    }
  }, [gl, setHovered, setSelected])

  useFrame(() => {
    const now = performance.now()

    // Track camera motion: damped orbit keeps drifting after the button is
    // released, and a readback during that drift drops frames just the same.
    if (!camera.position.equals(lastCameraPos.current)) {
      lastCameraPos.current.copy(camera.position)
      lastCameraMove.current = now
    }

    if (!pointer.current) return
    if (dragging.current) return
    if (now - lastCameraMove.current < SETTLE_MS) return
    if (now - lastPick.current < PICK_INTERVAL_MS) return
    lastPick.current = now

    // Deliberately *not* the same disc as the visible cloud. The picker
    // inflates it and floors it in pixels, which is what makes a three-pixel
    // node at the far side of the map as easy to hit as one under the nose.
    // See PICK_INFLATE and MIN_PICK_SIZE_PX.
    picker.setPointScale(BASE_POINT_SIZE, Math.min(gl.getPixelRatio(), 2))
    const index = picker.pick(
      gl,
      camera as THREE.PerspectiveCamera,
      pointer.current.x,
      pointer.current.y,
      size.width,
      size.height,
    )
    if (index !== hovered.current) {
      hovered.current = index
      setHovered(index)
      // The hand is the only thing that tells the reader a node is a target.
      // Without it the enlarged hitbox is invisible: they still aim at the
      // dot, still miss, and have no way to know they did not have to.
      gl.domElement.style.cursor = index === null ? '' : 'pointer'
    }
  })

  return null
}
