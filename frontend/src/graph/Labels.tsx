/**
 * Text on the map, strictly rationed.
 *
 * troika-three-text costs roughly a draw call per label, so labelling every
 * node is the fastest way to destroy the frame budget — at 4,000 nodes it is
 * fatal, and it was unreadable anyway: per-node titles overlapped into a wall
 * of text that hid the structure they annotated. Only cluster names are drawn,
 * a couple of dozen at most, and they double as the way into a region.
 */
import { Billboard, Text } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

import { prominentClusters } from '@/lib/density'
import { useGraphStore } from '@/state/graphStore'

/** Apparent height of a label, as a fraction of the viewport height. */
const LABEL_SCREEN_FRACTION = 0.021
const HOVER_GAIN = 1.14
/** Lift above the centroid, in the same screen-relative units. */
const LIFT = 1.8

/**
 * Cluster names at their centroids, and the way into a region.
 *
 * Always shown, unlike per-node titles: there are only a handful, and they are
 * what makes the map legible from a distance. Clicking one opens the region's
 * inspector, which is where the papers themselves are listed — annotating
 * every node in 3D produced overlapping text that hid the structure it was
 * describing.
 */
/**
 * Half-size of a label's exclusion box, in normalised device coordinates
 * (which span -1..1, so 0.08 is 4% of the viewport).
 *
 * Measured off a screenshot at 1600x767 rather than derived: a region name
 * renders around 250 px wide and wraps to two lines at ~55 px tall, and the
 * first attempt at half these numbers left "COGNITIVE MOTOR CONTROL" sitting
 * on top of "BIOLOGICAL COMPLEXITY".
 *
 * Wider than tall because a name is a line of text, not a dot. Generous in
 * both directions on purpose: a name that disappears slightly early costs the
 * reader one name, and two overlapping ones cost them both.
 */
const LABEL_HALF_WIDTH_NDC = 0.17
const LABEL_HALF_HEIGHT_NDC = 0.08

export function ClusterLabels() {
  const clusters = useGraphStore((s) => s.clusters)
  const centroids = useGraphStore((s) => s.clusterCentroids)
  const inspect = useGraphStore((s) => s.inspectCluster)
  const [hovered, setHovered] = useState<number | null>(null)

  // Names are rationed by what a viewport can hold, not by what the corpus
  // has. Forty names over forty regions is a wall of text with the map behind
  // it; the regions without one are still named on click.
  const named = useMemo(() => prominentClusters(clusters), [clusters])

  // Ranked once: the collision pass runs every frame and must not re-sort.
  const priority = useMemo(
    () => [...clusters].sort((a, b) => b.size - a.size).map((c) => c.id),
    [clusters],
  )
  const mounted = useRef(new Map<number, THREE.Group>())

  // Hide names that would land on top of each other.
  //
  // Rationing by *count* is not enough on its own, and the screenshot showed
  // exactly why: labels are sized in screen space, so they keep their size as
  // the camera pulls back while the centroids they sit on converge. Twelve
  // names that read cleanly up close become one illegible pile from a
  // distance. The test has to be against where they actually project, which
  // is only knowable per frame.
  //
  // Largest region first, so when two collide the one that survives is the one
  // describing more of the library.
  useFrame(({ camera }) => {
    const taken: Array<[number, number]> = []
    const centre = new THREE.Vector3()

    for (const id of priority) {
      const node = mounted.current.get(id)
      if (!node) continue

      centre.set(node.position.x, node.position.y, node.position.z)
      centre.project(camera)

      // Behind the camera, or off screen: not drawn, and not blocking
      // anything that is.
      if (centre.z > 1 || Math.abs(centre.x) > 1.3 || Math.abs(centre.y) > 1.3) {
        node.visible = false
        continue
      }

      const clear = taken.every(
        ([x, y]) =>
          Math.abs(centre.x - x) > LABEL_HALF_WIDTH_NDC ||
          Math.abs(centre.y - y) > LABEL_HALF_HEIGHT_NDC,
      )
      // A label the reader is pointing at never vanishes underneath them.
      node.visible = clear || hovered === id
      if (node.visible) taken.push([centre.x, centre.y])
    }
  })

  return (
    <>
      {clusters.map((cluster) => {
        const centre = centroids.get(cluster.id)
        if (!centre || !cluster.label || !named.has(cluster.id)) return null
        return (
          <ClusterLabel
            key={cluster.id}
            centre={centre}
            label={cluster.label}
            hovered={hovered === cluster.id}
            onOver={() => setHovered(cluster.id)}
            onOut={() => setHovered((h) => (h === cluster.id ? null : h))}
            onClick={() => inspect(cluster.id)}
            id={cluster.id}
            registry={mounted.current}
          />
        )
      })}
    </>
  )
}

function ClusterLabel({
  centre,
  label,
  hovered,
  onOver,
  onOut,
  onClick,
  id,
  registry,
}: {
  centre: [number, number, number]
  label: string
  hovered: boolean
  onOver: () => void
  onOut: () => void
  onClick: () => void
  id: number
  registry: Map<number, THREE.Group>
}) {
  const group = useRef<THREE.Group>(null)

  // Handed up so the parent can test every label's projected position in one
  // pass. The Map is passed rather than a callback because a callback defined
  // in the parent's render has a new identity every frame, and this effect
  // would then unregister and re-register on each one.
  useEffect(() => {
    const node = group.current
    if (node) registry.set(id, node)
    return () => {
      registry.delete(id)
    }
  }, [id, registry])

  // Sized in screen space rather than world space. A fixed world height means
  // apparent size grows with proximity: on the real corpus the nearest region
  // name spanned the entire viewport and was clipped at both edges, while the
  // far ones were unreadable. Labels on a map should stay the same size no
  // matter how close you get; only their position moves.
  useFrame(({ camera }) => {
    const node = group.current
    if (!node) return
    const distance = camera.position.distanceTo(
      new THREE.Vector3(centre[0], centre[1], centre[2]),
    )
    // Perspective cameras subtend a fixed angle, so apparent size is a pure
    // function of distance; the fov term converts the fraction into world units.
    const fov = ((camera as THREE.PerspectiveCamera).fov ?? 50) * (Math.PI / 180)
    const worldHeight = 2 * Math.tan(fov / 2) * distance
    const scale = worldHeight * LABEL_SCREEN_FRACTION * (hovered ? HOVER_GAIN : 1)
    node.scale.setScalar(scale)
    node.position.set(centre[0], centre[1] + scale * LIFT, centre[2])
  })

  return (
    <group ref={group}>
      <Billboard>
        <Text
          onClick={(event) => {
            event.stopPropagation()
            onClick()
          }}
          onPointerOver={onOver}
          onPointerOut={onOut}
          fontSize={1}
          color={hovered ? '#ffffff' : '#e8edf9'}
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.06}
          outlineColor="#050510"
          // In the label's own units, so long names wrap instead of running
          // off the sides of the screen.
          maxWidth={14}
          textAlign="center"
          fillOpacity={0.92}
        >
          {label.toUpperCase()}
        </Text>
      </Billboard>
    </group>
  )
}
