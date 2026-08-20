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
    }
    const onDown = () => {
      dragging.current = true
      // Whatever was hovered is not what the drag is about.
      if (hovered.current !== null) {
        hovered.current = null
        setHovered(null)
      }
    }
    const onUp = () => {
      dragging.current = false
    }
    const onClick = () => {
      // Select whatever is currently hovered: the pick already happened, so a
      // click never pays for a second readback.
      setSelected(hovered.current)
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

    picker.setPointScale(3.4, Math.min(gl.getPixelRatio(), 2))
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
    }
  })

  return null
}
