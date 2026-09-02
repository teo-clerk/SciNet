/**
 * Camera controls and animated focus.
 *
 * A selected node is flown to rather than jumped to: an instant cut loses the
 * viewer's sense of where they were, which is the one thing a spatial map is
 * for. The flight is eased and short enough not to feel like waiting.
 */
import { OrbitControls } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useCallback, useEffect, useRef } from 'react'
import * as THREE from 'three'

import { useGraphStore } from '@/state/graphStore'

const FLY_DURATION_MS = 850
/** How far to sit from a single paper. */
const VIEWING_DISTANCE = 14
/** How far to sit from a whole region. A cluster is spread out, so framing it
 *  at a node's distance puts the reader inside it looking at three papers. */
const CLUSTER_DISTANCE = 42
/** Degrees per second, roughly. Slow enough to read while it moves. */
const AUTO_ROTATE_SPEED = 0.35

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
}

export function CameraRig() {
  const controls = useRef<any>(null)
  const { camera } = useThree()
  const selectedIndex = useGraphStore((s) => s.selectedIndex)
  const inspectedCluster = useGraphStore((s) => s.inspectedCluster)
  const centroids = useGraphStore((s) => s.clusterCentroids)
  const buffers = useGraphStore((s) => s.buffers)
  const autoRotate = useGraphStore((s) => s.autoRotate)
  const cameraRequest = useGraphStore((s) => s.cameraRequest)

  const flight = useRef<{
    start: number
    fromPos: THREE.Vector3
    fromTarget: THREE.Vector3
    toPos: THREE.Vector3
    toTarget: THREE.Vector3
  } | null>(null)

  const flyTo = useCallback(
    (target: THREE.Vector3, distance: number) => {
      if (!controls.current) return
      // Approach along the current view direction so the flight reads as
      // moving closer rather than as an arbitrary teleport to a new angle.
      const direction = camera.position
        .clone()
        .sub(controls.current.target)
        .normalize()
        .multiplyScalar(distance)

      flight.current = {
        start: performance.now(),
        fromPos: camera.position.clone(),
        fromTarget: controls.current.target.clone(),
        toPos: target.clone().add(direction),
        toTarget: target,
      }
    },
    [camera],
  )

  useEffect(() => {
    if (selectedIndex === null || !buffers) return
    flyTo(
      new THREE.Vector3(
        buffers.positions[selectedIndex * 3]!,
        buffers.positions[selectedIndex * 3 + 1]!,
        buffers.positions[selectedIndex * 3 + 2]!,
      ),
      VIEWING_DISTANCE,
    )
  }, [selectedIndex, buffers, flyTo])

  // Opening a region flies to it. Without this, clicking a label opened the
  // inspector and left the camera wherever it was — so the reader got a list
  // of papers with no idea which part of the map they belonged to, which is
  // the one question the label was answering.
  useEffect(() => {
    if (inspectedCluster === null) return
    const centre = centroids.get(inspectedCluster)
    if (!centre) return
    flyTo(new THREE.Vector3(...centre), CLUSTER_DISTANCE)
  }, [inspectedCluster, centroids, flyTo])

  // The librarian (or anything else) may request a flight to an arbitrary
  // point. The nonce is the trigger: the same target twice must still fly,
  // and an effect keyed on coordinates alone would not re-fire.
  useEffect(() => {
    if (!cameraRequest) return
    flyTo(
      new THREE.Vector3(...cameraRequest.target),
      cameraRequest.distance,
    )
  }, [cameraRequest, flyTo])

  useFrame(() => {
    if (!controls.current) return

    // Driven here rather than through the prop, because whether a flight is in
    // progress lives in a ref: React never re-renders on it, so a prop reading
    // `flight.current === null` would be whatever it was at the last render.
    // A flight owns the camera while it runs — rotating at the same time drags
    // the target sideways underneath the interpolation and the arrival misses.
    controls.current.autoRotate = autoRotate && flight.current === null

    const f = flight.current
    if (!f) return

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
      autoRotateSpeed={AUTO_ROTATE_SPEED}
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
