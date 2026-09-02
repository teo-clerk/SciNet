/**
 * The 3D canvas.
 *
 * Depth cues (fog, size attenuation) carry more legibility in a 3D scatter
 * than detail does, so they are baseline rather than polish.
 */
import { Canvas } from '@react-three/fiber'
import * as THREE from 'three'

import { CameraRig } from './CameraRig'
import { Edges } from './Edges'
import { ClusterLabels } from './Labels'
import { Picker } from './Picker'
import { BridgeCurves } from './BridgeCurves'
import { PointCloud } from './PointCloud'
import { SkeletonEdges } from './SkeletonEdges'
import { Trail } from './Trail'
import { useGraphStore } from '@/state/graphStore'

export function Scene() {
  // While a morph is live the skeleton, labels, edges and bridges step
  // aside: they are properties of the ACTIVE run's layout and clustering,
  // and drawing them over the alternate model's opinion would attach run A's
  // chrome to positions it never described.
  const morphing = useGraphStore((s) => s.morphTarget !== null)
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{ position: [0, 0, 90], fov: 55, near: 0.1, far: 600 }}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      onCreated={({ scene, gl }) => {
        scene.background = new THREE.Color('#050510')
        gl.setClearColor('#050510')
      }}
    >
      {/* No scene fog: additive blending would add the fog colour rather than
          blend toward it, brightening the far side of the map. Depth is
          conveyed by the shaders' alpha falloff instead. */}
      {!morphing && <SkeletonEdges />}
      <PointCloud />
      {!morphing && <Trail />}
      {!morphing && <Edges />}
      {!morphing && <ClusterLabels />}
      {!morphing && <BridgeCurves />}
      <Picker />
      <CameraRig />
    </Canvas>
  )
}
