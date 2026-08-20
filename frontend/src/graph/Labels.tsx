/**
 * Text on the map, strictly rationed.
 *
 * troika-three-text costs roughly a draw call per label, so labelling every
 * node is the fastest way to destroy the frame budget — at 4,000 nodes it is
 * fatal, and it would be unreadable anyway. Labels are therefore limited to
 * what the viewer is actually looking at: the hovered and selected nodes, plus
 * the few nearest the point the camera is aimed at.
 */
import { Billboard, Text } from '@react-three/drei'
import { useFrame, useThree } from '@react-three/fiber'
import { useMemo, useRef, useState } from 'react'
import * as THREE from 'three'

import { useGraphStore } from '@/state/graphStore'

const MAX_TITLE_LABELS = 24
/**
 * Titles appear only once the camera is close enough for them to be readable.
 *
 * Pulled back over the whole corpus they are unreadable overlapping mush that
 * hides the clusters, and each one is a draw call — the render gate measured a
 * p95 of 29.5 ms with them always on, against a 16.7 ms budget. Cluster names
 * carry the overview; titles are for when you have arrived somewhere.
 */
const TITLE_VISIBLE_DISTANCE = 55
const RECOMPUTE_INTERVAL_MS = 180
const MAX_LABEL_CHARS = 46

function truncate(text: string | null): string {
  if (!text) return '(untitled)'
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > MAX_LABEL_CHARS
    ? `${clean.slice(0, MAX_LABEL_CHARS - 1)}…`
    : clean
}

export function TitleLabels() {
  const nodes = useGraphStore((s) => s.nodes)
  const buffers = useGraphStore((s) => s.buffers)
  const hoveredIndex = useGraphStore((s) => s.hoveredIndex)
  const selectedIndex = useGraphStore((s) => s.selectedIndex)

  const { camera } = useThree()
  const [visible, setVisible] = useState<number[]>([])
  const lastRun = useRef(0)
  const shown = useRef<number[]>([])
  const focus = useMemo(() => new THREE.Vector3(), [])

  useFrame(() => {
    const now = performance.now()
    if (now - lastRun.current < RECOMPUTE_INTERVAL_MS || !buffers) return
    lastRun.current = now

    // A point in front of the camera, not the camera itself: labels should
    // follow where the viewer is looking, not cling to the near plane.
    // Nothing is readable from across the corpus, so do not pay for it.
    if (camera.position.length() > TITLE_VISIBLE_DISTANCE) {
      if (shown.current.length) {
        shown.current = []
        setVisible([])
      }
      return
    }

    camera.getWorldDirection(focus)
    focus.multiplyScalar(30).add(camera.position)

    const scored: Array<[number, number]> = []
    for (let i = 0; i < nodes.length; i++) {
      if (buffers.filtered[i] === 0) continue
      const dx = buffers.positions[i * 3]! - focus.x
      const dy = buffers.positions[i * 3 + 1]! - focus.y
      const dz = buffers.positions[i * 3 + 2]! - focus.z
      scored.push([i, dx * dx + dy * dy + dz * dz])
    }
    scored.sort((a, b) => a[1] - b[1])

    const chosen = scored.slice(0, MAX_TITLE_LABELS).map(([i]) => i)
    if (hoveredIndex !== null && !chosen.includes(hoveredIndex)) chosen.push(hoveredIndex)
    if (selectedIndex !== null && !chosen.includes(selectedIndex)) chosen.push(selectedIndex)
    chosen.sort((a, b) => a - b)

    // Committing an identical set would re-render every troika Text five times
    // a second for nothing, which is exactly the cost this component exists to
    // avoid. Only publish a genuine change.
    const previous = shown.current
    if (
      previous.length !== chosen.length ||
      chosen.some((index, i) => previous[i] !== index)
    ) {
      shown.current = chosen
      setVisible(chosen)
    }
  })

  if (!buffers) return null

  return (
    <>
      {visible.map((index) => {
        const node = nodes[index]
        if (!node) return null
        const emphasised = index === selectedIndex || index === hoveredIndex
        return (
          <Billboard
            key={node.id}
            position={[
              buffers.positions[index * 3]!,
              buffers.positions[index * 3 + 1]! + 0.55,
              buffers.positions[index * 3 + 2]!,
            ]}
          >
            <Text
              fontSize={emphasised ? 0.62 : 0.4}
              color={emphasised ? '#ffffff' : '#93a0bd'}
              anchorX="center"
              anchorY="bottom"
              outlineWidth={emphasised ? 0.035 : 0.02}
              outlineColor="#070912"
              maxWidth={14}
            >
              {truncate(node.title)}
            </Text>
          </Billboard>
        )
      })}
    </>
  )
}

/**
 * Cluster names at their centroids.
 *
 * These are the labels that make the map readable at a glance, so unlike
 * titles they are always shown — there are only a couple of dozen.
 */
export function ClusterLabels() {
  const clusters = useGraphStore((s) => s.clusters)
  const centroids = useGraphStore((s) => s.clusterCentroids)

  return (
    <>
      {clusters.map((cluster) => {
        const centre = centroids.get(cluster.id)
        if (!centre || !cluster.label) return null
        return (
          <Billboard key={cluster.id} position={[centre[0], centre[1] + 2.2, centre[2]]}>
            <Text
              fontSize={1.15}
              color="#e8edf9"
              anchorX="center"
              anchorY="middle"
              outlineWidth={0.06}
              outlineColor="#070912"
              maxWidth={20}
              fillOpacity={0.92}
            >
              {cluster.label.toUpperCase()}
            </Text>
          </Billboard>
        )
      })}
    </>
  )
}
