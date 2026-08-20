# SciNet — working notes

Local, privacy-first 3D semantic map over a personal scientific PDF library.
Target scale 3–4k papers on a single laptop.

## Hard constraints

- **Python is pinned to 3.12.** `umap-learn` declares support only through 3.12
  and it is load-bearing. The system `python3` is 3.14 — never use it.
- **8 GB VRAM.** Marker+Surya (~3–4 GB), `qwen2.5vl:7b` (~6 GB), `qwen3:8b`
  (~5.5 GB) — no two of the large models fit at once. The worker holds a single
  GPU lock and batches **by job kind**, not per paper. Set
  `OLLAMA_MAX_LOADED_MODELS=1` and send `keep_alive: 0` after a batch.
- **The API never writes paper data.** The worker process is the sole writer.
  SQLite is in WAL mode with a 5 s busy timeout.
- **Enrichment is off by default.** Any outbound call goes through
  `services/metadata/enrich.py` and is written to `egress_log`.

## Non-obvious design decisions

- **Coordinates, not forces.** Node positions come from UMAP and mean something.
  A force-directed layout would fight that and cost CPU every frame.
- **Filter and "similar to" queries run in the 1024-D embedding space**, never on
  the rendered x/y/z. UMAP distorts global distance on purpose.
- **Dual embeddings.** A *document* vector (title + abstract + summary + section
  headings) drives the map; *chunk* vectors drive search. Mean-pooling a whole
  paper collapses the clusters toward a generic-academic-prose centroid.
- **Cluster on a separate 10-D UMAP**, not the 3-D display coordinates, which
  over-fragment under HDBSCAN.
- **Node data never enters React state.** Positions and per-node visuals are
  typed arrays mutated in place with `needsUpdate`; React renders only chrome.
- **Tag vocabulary is closed.** Unconstrained LLM tagging produces
  "deep learning" / "Deep Learning" / "DL" as three tags. New tags land in
  `pending_review` and merge by label-embedding similarity.
- **Provenance everywhere.** Derived rows carry `model_id` / `pipeline_version`.
  Swapping the embedding model invalidates vectors and projections but **not**
  markdown.

## Layout

```
backend/app/
  core/      config, db (WAL pragmas), paths (traversal guard), gpu, events
  models/    paper · tagging · projection · system   (all re-exported in __init__)
  routers/   thin HTTP; no business logic
  services/  ingest · parse · metadata · embed · tagging · project
  workers/   queue (SQLite-backed) · runner · handlers
frontend/src/
  graph/     Scene · PointCloud (one THREE.Points, one draw call) · shaders
  panels/    sidebar, filters, search, status
  state/     zustand stores — buffers live here, outside React
```

## Conventions

- ruff, line length 88; `from __future__ import annotations` in every module.
- Files 200–400 lines typical, 800 max. Split pipeline code by *stage*.
- Immutable style: return new objects rather than mutating — the one deliberate
  exception is the render hot path, where in-place typed-array writes are the point.
- Tests live in `backend/tests/`, fixtures in `backend/tests/fixtures/`.

## Measured numbers (not estimates)

- tier-0 probe: **~2 ms/page**; tier-0 Markdown conversion: **~225 ms/page**.
  The 100x gap is why the router defers conversion until a tier has won.
- `pymupdf4llm.to_markdown` must be called with `use_ocr=False`. Left on, it
  runs Tesseract inside the "cheap CPU tier", costs ~29% more, and destroys the
  meaning of the quality gate.
- Browser WebGL runs on the **Intel Arc iGPU**, not the RTX 4060, under hybrid
  graphics. Good: the UI never competes with the worker for VRAM.
- 4,000 nodes render at a p95 frame of ~17 ms (vsync-locked, no drops).

## Commands

```bash
cd backend && uv run pytest                     # tests
cd backend && uv run python -m app.workers.runner   # worker
cd backend && uv run python ../scripts/backfill.py  # bulk import
bun scripts/verify_render.mjs http://localhost:5173  # 3D render gate
cd backend && uv run ruff check . && uv run ruff format --check .
cd backend && uv run alembic revision --autogenerate -m "..."
cd backend && uv run alembic upgrade head
cd frontend && bun run typecheck
```
