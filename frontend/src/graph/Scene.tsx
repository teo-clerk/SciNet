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

export function Scene() {
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{ position: [0, 0, 90], fov: 55, near: 0.1, far: 600 }}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      onCreated={({ scene, gl }) => {
        scene.background = new THREE.Color('#070912')
        gl.setClearColor('#070912')
      }}
    >
      <fog attach="fog" args={['#070912', 80, 300]} />
      <PointCloud />
      <Edges />
      <ClusterLabels />
      <TitleLabels />
      <Picker />
      <CameraRig />
    </Canvas>
  )
}
