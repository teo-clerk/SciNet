/**
 * Graph state.
 *
 * Node data lives OUTSIDE the React tree. Positions and per-node visual
 * attributes are plain typed arrays the renderer mutates in place; React
 * renders only the chrome. Mapping 300–4,000 nodes to components is the
 * single decision that separates 60 fps from 6.
 *
 * Selectors here are deliberately coarse — components subscribe to scalars
 * (a hovered id, a colour mode) rather than to the buffers, so a hover never
 * re-renders anything that draws.
 */
import { create } from 'zustand'

import type { DecodedGraph, GraphCluster, GraphNode } from '@/api/graph'
import type { SearchMode } from '@/lib/search'

export type ColorMode = 'cluster' | 'year' | 'provisional'
export type ViewMode = 'map' | 'list'
export type SortKey = 'title' | 'year' | 'cluster' | 'confidence'

export interface GraphBuffers {
  positions: Float32Array
  colors: Float32Array
  sizes: Float32Array
  /** 0 = filtered out (dimmed), 1 = visible. */
  filtered: Float32Array
  /** 0 | 0.6 (hovered) | 1 (selected). */
  selected: Float32Array
}

interface GraphState {
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string | null

  runId: number | null
  method: string | null
  count: number
  nodes: GraphNode[]
  clusters: GraphCluster[]
  tagVocabulary: string[]
  buffers: GraphBuffers | null
  skeleton: Uint32Array | null
  /** Cluster centroids in world space, for floating labels. */
  clusterCentroids: Map<number, [number, number, number]>

  colorMode: ColorMode
  view: ViewMode
  sortKey: SortKey
  sortAscending: boolean
  hoveredIndex: number | null
  selectedIndex: number | null
  query: string
  searchMode: SearchMode
  /** Paper ids returned by the active search, or null when none is running. */
  searchResults: Set<number> | null
  searchPending: boolean
  searchError: string | null
  /** Model still loading — a wait, not a fault. */
  searchWarming: { remaining: number | null } | null
  activeTags: Set<number>

  setLoading: () => void
  setError: (message: string) => void
  setGraph: (graph: DecodedGraph) => void
  setColorMode: (mode: ColorMode) => void
  setView: (view: ViewMode) => void
  setSort: (key: SortKey) => void
  setHovered: (index: number | null) => void
  setSelected: (index: number | null) => void
  setQuery: (query: string) => void
  setSearchMode: (mode: SearchMode) => void
  setSearchResults: (ids: Set<number> | null) => void
  setSearchPending: (pending: boolean) => void
  setSearchError: (message: string | null) => void
  setSearchWarming: (state: { remaining: number | null } | null) => void
  toggleTag: (tagId: number) => void
  clearFilters: () => void
}

/** Distinct hues that stay legible against a near-black ground. */
export const CLUSTER_PALETTE: [number, number, number][] = [
  [0.36, 0.72, 1.0], [1.0, 0.55, 0.35], [0.55, 0.9, 0.55],
  [0.92, 0.5, 0.78], [1.0, 0.83, 0.4], [0.6, 0.62, 1.0],
  [0.4, 0.9, 0.85], [0.95, 0.42, 0.45], [0.75, 0.85, 0.4],
  [0.7, 0.5, 0.95], [0.45, 0.8, 0.65], [0.9, 0.65, 0.5],
]
/** Noise: present but visually recessive, never competing with a real cluster. */
export const NOISE_COLOR: [number, number, number] = [0.38, 0.4, 0.48]
/** Placed by transform() rather than a full fit — worth seeing at a glance. */
export const PROVISIONAL_COLOR: [number, number, number] = [1.0, 0.72, 0.2]
export const SETTLED_COLOR: [number, number, number] = [0.3, 0.45, 0.62]

export function yearColor(year: number | null, min: number, max: number) {
  if (year === null) return NOISE_COLOR
  const t = max > min ? (year - min) / (max - min) : 0.5
  // Cool (old) to warm (recent); monotonic in lightness so it reads without a key.
  return [0.25 + 0.7 * t, 0.45 + 0.25 * (1 - Math.abs(t - 0.5) * 2), 1.0 - 0.65 * t] as [
    number,
    number,
    number,
  ]
}

function buildBuffers(graph: DecodedGraph): GraphBuffers {
  const n = graph.nodes.length
  return {
    positions: graph.positions,
    colors: new Float32Array(n * 3),
    sizes: new Float32Array(n).fill(1),
    filtered: new Float32Array(n).fill(1),
    selected: new Float32Array(n),
  }
}

function centroidsOf(graph: DecodedGraph): Map<number, [number, number, number]> {
  const sums = new Map<number, [number, number, number, number]>()
  graph.nodes.forEach((node, i) => {
    if (node.cluster === null) return
    const acc = sums.get(node.cluster) ?? [0, 0, 0, 0]
    acc[0] += graph.positions[i * 3]!
    acc[1] += graph.positions[i * 3 + 1]!
    acc[2] += graph.positions[i * 3 + 2]!
    acc[3] += 1
    sums.set(node.cluster, acc)
  })

  const out = new Map<number, [number, number, number]>()
  for (const [id, [x, y, z, n]] of sums) out.set(id, [x / n, y / n, z / n])
  return out
}

export const useGraphStore = create<GraphState>((set, get) => ({
  status: 'idle',
  error: null,
  runId: null,
  method: null,
  count: 0,
  nodes: [],
  clusters: [],
  tagVocabulary: [],
  buffers: null,
  skeleton: null,
  clusterCentroids: new Map(),
  colorMode: 'cluster',
  view: 'map',
  sortKey: 'title',
  sortAscending: true,
  hoveredIndex: null,
  selectedIndex: null,
  query: '',
  searchMode: 'title',
  searchResults: null,
  searchPending: false,
  searchError: null,
  searchWarming: null,
  activeTags: new Set(),

  setLoading: () => set({ status: 'loading', error: null }),
  setError: (error) => set({ status: 'error', error }),

  setGraph: (graph) =>
    set({
      status: 'ready',
      error: null,
      runId: graph.runId,
      method: graph.method,
      count: graph.nodes.length,
      nodes: graph.nodes,
      clusters: graph.clusters,
      tagVocabulary: graph.tagVocabulary,
      buffers: buildBuffers(graph),
      skeleton: graph.skeleton,
      clusterCentroids: centroidsOf(graph),
      hoveredIndex: null,
      selectedIndex: null,
      searchResults: null,
      searchError: null,
    }),

  setColorMode: (colorMode) => set({ colorMode }),
  setView: (view) => set({ view }),
  setSort: (key) =>
    set((state) =>
      // Clicking the active column reverses it; a different column starts
      // ascending, which is what a reader expects from a table.
      state.sortKey === key
        ? { sortAscending: !state.sortAscending }
        : { sortKey: key, sortAscending: true },
    ),
  setHovered: (hoveredIndex) => {
    if (get().hoveredIndex !== hoveredIndex) set({ hoveredIndex })
  },
  setSelected: (selectedIndex) => set({ selectedIndex }),
  setQuery: (query) => set({ query, searchError: null }),
  setSearchMode: (searchMode) =>
    set({ searchMode, searchResults: null, searchError: null }),
  setSearchResults: (searchResults) => set({ searchResults }),
  setSearchPending: (searchPending) => set({ searchPending }),
  setSearchError: (searchError) =>
    set({ searchError, searchResults: null, searchWarming: null }),
  setSearchWarming: (searchWarming) => set({ searchWarming, searchError: null }),
  toggleTag: (tagId) =>
    set((state) => {
      const next = new Set(state.activeTags)
      next.has(tagId) ? next.delete(tagId) : next.add(tagId)
      return { activeTags: next }
    }),
  clearFilters: () =>
    set({
      query: '',
      activeTags: new Set(),
      searchResults: null,
      searchError: null,
    }),
}))
