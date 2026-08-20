/**
 * The 3D canvas: camera, controls, ambient depth cues, and the point cloud.
 *
 * Depth cues (fog, size attenuation) carry more legibility in a 3D scatter than
 * detail does, so they are part of the baseline rather than polish.
 */
import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import * as THREE from 'three'

import { PointCloud } from './PointCloud'

export function Scene() {
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{ position: [0, 0, 85], fov: 55, near: 0.1, far: 400 }}
      gl={{ antialias: true, powerPreference: 'high-performance' }}
      onCreated={({ scene, gl }) => {
        scene.background = new THREE.Color('#070912')
        gl.setClearColor('#070912')
      }}
    >
      <fog attach="fog" args={['#070912', 60, 180]} />
      <PointCloud />
      <OrbitControls
        enableDamping
        dampingFactor={0.08}
        rotateSpeed={0.6}
        zoomSpeed={0.8}
        minDistance={5}
        maxDistance={220}
        makeDefault
      />
    </Canvas>
  )
}
