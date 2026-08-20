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
import { ClusterLabels, TitleLabels } from './Labels'
import { Picker } from './Picker'
import { PointCloud } from './PointCloud'
import { SkeletonEdges } from './SkeletonEdges'

export function Scene() {
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
      <SkeletonEdges />
      <PointCloud />
      <Edges />
      <ClusterLabels />
      <TitleLabels />
      <Picker />
      <CameraRig />
    </Canvas>
  )
}
