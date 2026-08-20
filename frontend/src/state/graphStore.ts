/**
 * Graph data store.
 *
 * Node data deliberately lives OUTSIDE the React tree. Positions and per-node
 * visual attributes are plain typed arrays that the renderer mutates in place;
 * React only ever renders the chrome. This is the single rule that separates
 * 60 fps from 6 at 4k nodes.
 */
import { create } from 'zustand'

export interface NodeMeta {
  id: number
  title: string
  clusterId: number
  year: number | null
}

export interface GraphBuffers {
  /** xyz triples, length = count * 3 */
  positions: Float32Array
  /** rgb triples, length = count * 3 */
  colors: Float32Array
  /** per-node size multiplier */
  sizes: Float32Array
  /** 0 = filtered out, 1 = visible */
  filtered: Float32Array
  /** 0 | 1 */
  selected: Float32Array
}

interface GraphState {
  count: number
  buffers: GraphBuffers | null
  meta: NodeMeta[]
  hoveredIndex: number | null
  selectedIndex: number | null

  setGraph: (buffers: GraphBuffers, meta: NodeMeta[]) => void
  setHovered: (index: number | null) => void
  setSelected: (index: number | null) => void
}

export const useGraphStore = create<GraphState>((set) => ({
  count: 0,
  buffers: null,
  meta: [],
  hoveredIndex: null,
  selectedIndex: null,

  setGraph: (buffers, meta) =>
    set({ buffers, meta, count: meta.length, selectedIndex: null, hoveredIndex: null }),

  setHovered: (hoveredIndex) => set({ hoveredIndex }),

  setSelected: (selectedIndex) => set({ selectedIndex }),
}))

/** Palette for cluster colouring — distinct hues, readable on a dark ground. */
export const CLUSTER_PALETTE: [number, number, number][] = [
  [0.36, 0.72, 1.0],
  [1.0, 0.55, 0.35],
  [0.55, 0.9, 0.55],
  [0.92, 0.5, 0.78],
  [1.0, 0.83, 0.4],
  [0.6, 0.62, 1.0],
  [0.4, 0.9, 0.85],
  [0.95, 0.42, 0.45],
  [0.75, 0.85, 0.4],
  [0.7, 0.5, 0.95],
]
