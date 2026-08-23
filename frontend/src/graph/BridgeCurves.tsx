/**
 * Lines between regions that share papers.
 *
 * Only drawn where the backend found both centroid proximity *and* papers that
 * sit between the two clusters, and only where the LLM, asked directly, said
 * the link is real. Each curve therefore stands for a claim someone can check
 * by opening the bridging papers — which is why hovering one shows them.
 *
 * Bowed rather than straight: a straight line between two centroids passes
 * through the points in between and reads as an edge belonging to them.
 */
import { Html, Line } from '@react-three/drei'
import { useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'

import { fetchClusterLinks, type ClusterLink } from '@/api/clusters'
import { visibleBridges } from '@/lib/density'
import { useGraphStore } from '@/state/graphStore'

/** Bridges are the map's argument about how its regions relate, and at one
 *  device pixel they were a hairline nobody traced. These are set so a
 *  connection reads across a dark background from across the room, while
 *  staying dimmer than the nodes it joins — the papers are the subject and the
 *  bridge is the claim about them. */
const BRIDGE_COLOR = '#7ea8e8'
const BRIDGE_ACTIVE = '#cfe4ff'
/** World-space width in Line2's units, roughly device pixels at this scale. */
const BRIDGE_WIDTH = 1.8
const ACTIVE_WIDTH = 3.2
/** Multiplied by each bridge's relative strength, 0.35 to 1. */
const BRIDGE_OPACITY = 0.75

const CURVE_SEGMENTS = 32
/** How far the midpoint lifts off the straight chord, as a fraction of span. */
const BOW = 0.18

type Drawn = {
  link: ClusterLink
  /** The curve, sampled. Shared by the drawn line and the pick target. */
  points: THREE.Vector3[]
  /** Where the hover card sits: the apex of the bow, not the chord midpoint. */
  apex: [number, number, number]
  /** How strong this relationship is relative to the others being drawn,
   *  0.35 to 1. Carried into opacity so the map shows the difference. */
  weight: number
}

export function BridgeCurves() {
  const centroids = useGraphStore((s) => s.clusterCentroids)
  const inspect = useGraphStore((s) => s.inspectCluster)
  const [links, setLinks] = useState<ClusterLink[]>([])
  const [hovered, setHovered] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchClusterLinks().then((l) => !cancelled && setLinks(l))
    return () => {
      cancelled = true
    }
  }, [centroids])

  const drawn = useMemo<Drawn[]>(() => {
    const out: Drawn[] = []
    // Each bridge is a curve across the whole map, so they cross rather than
    // tile: past a dozen the map is behind a net. The weakest drawn one is
    // faint rather than absent, so a weak relationship still reads as weak
    // instead of as a missing one — and the inspector lists all of them.
    for (const { link, weight } of visibleBridges(links)) {
      const a = centroids.get(link.source_id)
      const b = centroids.get(link.target_id)
      if (!a || !b) continue

      const from = new THREE.Vector3(...a)
      const to = new THREE.Vector3(...b)
      const mid = from.clone().lerp(to, 0.5)
      // Bow away from the origin so the curve arcs around the cloud's bulk
      // rather than diving through its centre.
      const lift = mid.clone().normalize().multiplyScalar(from.distanceTo(to) * BOW)
      const curve = new THREE.QuadraticBezierCurve3(from, mid.add(lift), to)
      const apex = curve.getPoint(0.5)
      out.push({
        link,
        // Sampled once and shared: the drawn line and the invisible tube that
        // catches the pointer must follow the same path, or the bridge is not
        // where it looks like it is.
        points: curve.getPoints(CURVE_SEGMENTS),
        apex: [apex.x, apex.y, apex.z],
        weight,
      })
    }
    return out
  }, [links, centroids])

  // No geometry disposal here any more: the curves are plain Vector3 arrays,
  // and the GPU buffers behind <Line> and <tubeGeometry> are owned by
  // react-three-fiber, which frees them when the element unmounts.

  return (
    <>
      {drawn.map(({ link, points, weight }, i) => {
        const active = hovered === i
        return (
          <group key={`${link.source_id}-${link.target_id}`}>
            {/* Line rather than <line>: WebGL ignores lineWidth on a native
                line, so every connection was one device pixel however much
                the material asked for — at 1600px wide that is a hairline the
                reader has to hunt for. Line2 draws the same curve as camera-
                facing quads, so the width is real. */}
            <Line
              points={points}
              color={active ? BRIDGE_ACTIVE : BRIDGE_COLOR}
              lineWidth={active ? ACTIVE_WIDTH : BRIDGE_WIDTH}
              transparent
              opacity={active ? 1 : BRIDGE_OPACITY * weight}
              blending={THREE.AdditiveBlending}
              depthWrite={false}
            />
            {/* A 1px line is impossible to hit, so pick against a fat
                invisible tube laid along the same curve. */}
            <mesh
              onPointerOver={() => setHovered(i)}
              onPointerOut={() => setHovered((h) => (h === i ? null : h))}
              onClick={(event) => {
                event.stopPropagation()
                inspect(link.source_id)
              }}
            >
              <tubeGeometry
                args={[
                  new THREE.CatmullRomCurve3(points),
                  CURVE_SEGMENTS,
                  0.45,
                  6,
                  false,
                ]}
              />
              <meshBasicMaterial visible={false} />
            </mesh>
          </group>
        )
      })}

      {hovered !== null && drawn[hovered] && (
        <BridgeCard link={drawn[hovered].link} at={drawn[hovered].apex} />
      )}
    </>
  )
}

/** The claim behind a curve, and the papers it rests on. */
function BridgeCard({
  link,
  at,
}: {
  link: ClusterLink
  at: [number, number, number]
}) {
  return (
    <Html center position={at} style={{ pointerEvents: 'none' }}>
      <div className="bridge-card">
        <h4>
          {link.source_label} <span className="dim">↔</span> {link.target_label}
        </h4>
        {link.summary && <p>{link.summary}</p>}
        {link.bridge_papers.length > 0 && (
          <ul>
            {link.bridge_papers.slice(0, 4).map((p) => (
              <li key={p.paper_id}>{p.title ?? `#${p.paper_id}`}</li>
            ))}
          </ul>
        )}
      </div>
    </Html>
  )
}
