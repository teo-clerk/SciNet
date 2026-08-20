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

export function Picker() {
  const { gl, camera, scene, size } = useThree()
  const setHovered = useGraphStore((s) => s.setHovered)
  const setSelected = useGraphStore((s) => s.setSelected)
  const buffers = useGraphStore((s) => s.buffers)

  const picker = useMemo(() => new GpuPicker(), [])
  const pointer = useRef<{ x: number; y: number } | null>(null)
  const lastPick = useRef(0)
  const hovered = useRef<number | null>(null)

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
      setHovered(null)
    }
    const onClick = () => {
      // Select whatever is currently hovered: the pick already happened, so a
      // click never pays for a second readback.
      setSelected(hovered.current)
    }

    canvas.addEventListener('pointermove', onMove)
    canvas.addEventListener('pointerleave', onLeave)
    canvas.addEventListener('click', onClick)
    return () => {
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerleave', onLeave)
      canvas.removeEventListener('click', onClick)
    }
  }, [gl, setHovered, setSelected])

  useFrame(() => {
    const now = performance.now()
    if (!pointer.current || now - lastPick.current < PICK_INTERVAL_MS) return
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
