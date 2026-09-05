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
import { LAB_KEY, readLabPref, writePref } from '@/lib/prefs'
import type { SearchMode } from '@/lib/search'

export type ColorMode = 'cluster' | 'year' | 'provisional'
export type ViewMode = 'map' | 'list' | 'quarantine' | 'models' | 'review'
export type SortKey = 'title' | 'year' | 'cluster' | 'confidence'

export interface GraphBuffers {
  positions: Float32Array
  colors: Float32Array
  sizes: Float32Array
  /** 0 = filtered out (dimmed), 1 = visible. */
  filtered: Float32Array
  /** 0 | 0.6 (hovered) | 1 (selected). */
  selected: Float32Array
  /** 1 = the librarian is drawing attention here; pulsed by the shader. */
  highlight: Float32Array
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
  /** Show the numbers behind the map — run ids, confidences, frame times.
   *  Off for a reader; on for whoever is tuning the pipeline. Remembered per
   *  browser, and forced either way by `?lab=1` / `?lab=0`. */
  labMode: boolean
  autoRotate: boolean
  sortKey: SortKey
  sortAscending: boolean
  hoveredIndex: number | null
  selectedIndex: number | null
  /** Cluster whose inspector is open, or null. */
  inspectedCluster: number | null
  query: string
  searchMode: SearchMode
  /** Paper ids returned by the active search, or null when none is running. */
  searchResults: Set<number> | null
  searchPending: boolean
  searchError: string | null
  /** Model still loading — a wait, not a fault. */
  searchWarming: { remaining: number | null } | null
  activeTags: Set<number>
  /** Show only papers up to this year; null = the whole timeline. */
  yearCutoff: number | null
  /** Paper ids matching the quantity range filter; null = no filter. */
  quantityResults: Set<number> | null
  /** Morph view: where each node goes at t=1, in active-run index space. */
  morphTarget: Float32Array | null
  /** The active layout, copied when a morph begins, restored when it ends. */
  morphBase: Float32Array | null
  /** 0 = the active layout, 1 = the alternate model's opinion. */
  morphT: number
  morphRunId: number | null
  /** The librarian panel's visibility; its transcript lives in the panel. */
  librarianOpen: boolean
  /** Node indices the librarian is pointing at; null = none. */
  highlightSet: Set<number> | null
  /** Node indices, in order, forming the librarian's trail. Empty = none. */
  trail: number[]
  /** Pending camera flight; the nonce lets the same target fire twice. */
  cameraRequest: {
    target: [number, number, number]
    distance: number
    nonce: number
  } | null

  setLoading: () => void
  setError: (message: string) => void
  setGraph: (graph: DecodedGraph) => void
  setColorMode: (mode: ColorMode) => void
  setView: (view: ViewMode) => void
  setLabMode: (on: boolean) => void
  toggleAutoRotate: () => void
  setSort: (key: SortKey) => void
  setHovered: (index: number | null) => void
  setSelected: (index: number | null) => void
  inspectCluster: (clusterId: number | null) => void
  setQuery: (query: string) => void
  setSearchMode: (mode: SearchMode) => void
  setSearchResults: (ids: Set<number> | null) => void
  setSearchPending: (pending: boolean) => void
  setSearchError: (message: string | null) => void
  setSearchWarming: (state: { remaining: number | null } | null) => void
  toggleTag: (tagId: number) => void
  setYearCutoff: (year: number | null) => void
  setQuantityResults: (ids: Set<number> | null) => void
  setMorph: (runId: number, target: Float32Array, base: Float32Array) => void
  setMorphT: (t: number) => void
  clearMorph: () => void
  toggleLibrarian: () => void
  setHighlight: (indices: Set<number> | null) => void
  setTrail: (indices: number[]) => void
  flyToPoint: (target: [number, number, number], distance: number) => void
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
    highlight: new Float32Array(n),
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
  labMode: readLabPref(),
  autoRotate: false,
  sortKey: 'title',
  sortAscending: true,
  hoveredIndex: null,
  selectedIndex: null,
  inspectedCluster: null,
  query: '',
  searchMode: 'title',
  searchResults: null,
  searchPending: false,
  searchError: null,
  searchWarming: null,
  activeTags: new Set(),
  yearCutoff: null,
  quantityResults: null,
  morphTarget: null,
  morphBase: null,
  morphT: 0,
  morphRunId: null,
  librarianOpen: false,
  highlightSet: null,
  trail: [],
  cameraRequest: null,

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
      yearCutoff: null,
      quantityResults: null,
      morphTarget: null,
      morphBase: null,
      morphT: 0,
      morphRunId: null,
      highlightSet: null,
      trail: [],
      cameraRequest: null,
    }),

  setColorMode: (colorMode) => set({ colorMode }),
  setView: (view) => set({ view }),
  setLabMode: (on) => {
    writePref(LAB_KEY, on ? '1' : '0')
    set((state) => {
      // The Models and Review views, and the Provisional colouring, are lab
      // furniture: their buttons go with the lab. Turning it off while
      // standing in one would leave the reader in a room with no door.
      const inLabView = state.view === 'models' || state.view === 'review'
      return {
        labMode: on,
        view: !on && inLabView ? 'map' : state.view,
        colorMode: !on && state.colorMode === 'provisional' ? 'cluster' : state.colorMode,
      }
    })
  },
  toggleAutoRotate: () => set((state) => ({ autoRotate: !state.autoRotate })),
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
  // Region and paper are independent, and deliberately so. Picking a paper out
  // of a cluster's list must not close the list — the reader is working
  // through it, and losing their place after every click makes the list
  // useless for the one thing it is for. They occupy opposite sides of the
  // canvas so both can be read at once.
  setSelected: (selectedIndex) => set({ selectedIndex }),
  inspectCluster: (inspectedCluster) => set({ inspectedCluster }),
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
  setYearCutoff: (yearCutoff) => set({ yearCutoff }),
  setQuantityResults: (quantityResults) => set({ quantityResults }),
  setMorph: (morphRunId, morphTarget, morphBase) =>
    set({ morphRunId, morphTarget, morphBase, morphT: 0 }),
  setMorphT: (morphT) => set({ morphT }),
  clearMorph: () =>
    set({ morphTarget: null, morphRunId: null, morphT: 0 }),
  toggleLibrarian: () => set((state) => ({ librarianOpen: !state.librarianOpen })),
  setHighlight: (highlightSet) => set({ highlightSet }),
  setTrail: (trail) => set({ trail }),
  flyToPoint: (target, distance) =>
    set((state) => ({
      cameraRequest: {
        target,
        distance,
        nonce: (state.cameraRequest?.nonce ?? 0) + 1,
      },
    })),
  clearFilters: () =>
    set({
      query: '',
      activeTags: new Set(),
      searchResults: null,
      searchError: null,
      yearCutoff: null,
      quantityResults: null,
      morphTarget: null,
      morphBase: null,
      morphT: 0,
      morphRunId: null,
      highlightSet: null,
      trail: [],
      cameraRequest: null,
    }),
}))
