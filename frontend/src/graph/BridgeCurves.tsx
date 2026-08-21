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
import { Html } from '@react-three/drei'
import { useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'

import { fetchClusterLinks, type ClusterLink } from '@/api/clusters'
import { useGraphStore } from '@/state/graphStore'

const CURVE_SEGMENTS = 32
/** How far the midpoint lifts off the straight chord, as a fraction of span. */
const BOW = 0.18

type Drawn = {
  link: ClusterLink
  geometry: THREE.BufferGeometry
  /** Where the hover card sits: the apex of the bow, not the chord midpoint. */
  apex: [number, number, number]
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
    for (const link of links) {
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
      const geometry = new THREE.BufferGeometry().setFromPoints(
        curve.getPoints(CURVE_SEGMENTS),
      )
      const apex = curve.getPoint(0.5)
      out.push({ link, geometry, apex: [apex.x, apex.y, apex.z] })
    }
    return out
  }, [links, centroids])

  useEffect(() => {
    // Geometries are not garbage collected: they hold GPU buffers.
    return () => drawn.forEach(({ geometry }) => geometry.dispose())
  }, [drawn])

  return (
    <>
      {drawn.map(({ link, geometry }, i) => {
        const active = hovered === i
        return (
          <line key={`${link.source_id}-${link.target_id}`}>
            <primitive object={geometry} attach="geometry" />
            <lineBasicMaterial
              color={active ? '#9fd0ff' : '#4a6fa5'}
              transparent
              opacity={active ? 0.85 : 0.32}
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
                  new THREE.CatmullRomCurve3(
                    Array.from(
                      { length: CURVE_SEGMENTS + 1 },
                      (_, k) =>
                        new THREE.Vector3().fromBufferAttribute(
                          geometry.getAttribute('position') as THREE.BufferAttribute,
                          k,
                        ),
                    ),
                  ),
                  CURVE_SEGMENTS,
                  0.45,
                  6,
                  false,
                ]}
              />
              <meshBasicMaterial visible={false} />
            </mesh>
          </line>
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
