/**
 * Camera controls and animated focus.
 *
 * A selected node is flown to rather than jumped to: an instant cut loses the
 * viewer's sense of where they were, which is the one thing a spatial map is
 * for. The flight is eased and short enough not to feel like waiting.
 */
import { OrbitControls } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useRef } from 'react'
import * as THREE from 'three'

import { useGraphStore } from '@/state/graphStore'

const FLY_DURATION_MS = 850
const VIEWING_DISTANCE = 14

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
}

export function CameraRig() {
  const controls = useRef<any>(null)
  const { camera } = useThree()
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const buffers = useGraphStore((s) => s.buffers)

  const flight = useRef<{
    start: number
    fromPos: THREE.Vector3
    fromTarget: THREE.Vector3
    toPos: THREE.Vector3
    toTarget: THREE.Vector3
  } | null>(null)

  useEffect(() => {
    if (selectedIndex === null || !buffers || !controls.current) return

    const target = new THREE.Vector3(
      buffers.positions[selectedIndex * 3]!,
      buffers.positions[selectedIndex * 3 + 1]!,
      buffers.positions[selectedIndex * 3 + 2]!,
    )
    // Approach along the current view direction so the flight reads as moving
    // closer rather than as an arbitrary teleport to a new angle.
    const direction = camera.position
      .clone()
      .sub(controls.current.target)
      .normalize()
      .multiplyScalar(VIEWING_DISTANCE)

    flight.current = {
      start: performance.now(),
      fromPos: camera.position.clone(),
      fromTarget: controls.current.target.clone(),
      toPos: target.clone().add(direction),
      toTarget: target,
    }
  }, [selectedIndex, buffers, camera])

  useFrame(() => {
    const f = flight.current
    if (!f || !controls.current) return

    const t = Math.min((performance.now() - f.start) / FLY_DURATION_MS, 1)
    const eased = easeInOutCubic(t)

    camera.position.lerpVectors(f.fromPos, f.toPos, eased)
    controls.current.target.lerpVectors(f.fromTarget, f.toTarget, eased)
    controls.current.update()

    if (t >= 1) flight.current = null
  })

  return (
    <OrbitControls
      ref={controls}
      enableDamping
      dampingFactor={0.08}
      rotateSpeed={0.55}
      zoomSpeed={0.8}
      panSpeed={0.7}
      minDistance={3}
      maxDistance={400}
      makeDefault
    />
  )
}
