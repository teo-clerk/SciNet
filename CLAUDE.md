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
- **Property order is load-bearing in constrained decoding.** Ollama's `format`
  schema fills fields in declaration order, so a verdict field must be declared
  *before* the prose it governs. `BRIDGE_SCHEMA` asked for `relationship` first
  and every bridge on the real corpus came back with the literal string
  `"connected"` — the model had not decided anything yet and emitted filler.
  Reordering it to `connected` then `relationship` produced real sentences from
  the same model and prompt. `NAME_SCHEMA` is the deliberate opposite: its
  `confident` flag is a self-assessment of the name already written, so it
  comes last.
- **A cluster may not hold more than half the corpus** (`MAX_CLUSTER_SHARE`).
  HDBSCAN's `relative_validity_` measures separation, not usefulness, and will
  rank "one tight region plus a bag holding everything else" above a real
  decomposition: on the 57-paper corpus it scored a 35-paper catch-all at 0.654
  as the best split, having preferred eight genuine regions at 0.732 one run
  earlier, after only two vectors changed. Floors producing a dominant cluster
  are used only when nothing else clusters at all.
- **The library is not all PDFs.** `.txt`, `.md`, `.docx`, `.epub`, `.mobi`,
  `.azw3` and `.djvu` skip tier escalation entirely — that machinery exists
  because a PDF's text layer may not be recoverable, which is not a question
  these formats have. They report tier 0 with their own parser name and enter
  the identical metadata → embed → project → tag path.
- **EPUB is unzipped here; MOBI is handed to MuPDF.** An EPUB's headings are
  marked up semantically and its package document holds a real title, author
  and date, all of which beat anything inferred from layout — so it is read
  directly with `zipfile` + `html.parser` and no new dependency. MOBI/AZW3
  payloads are PalmDOC/KF8-compressed, which MuPDF already decompresses
  correctly, so reimplementing it would be a lot of work to reach the same
  text.
- **DjVu text is decoded in pure Python** (`parse/djvu_bzz.py`). The OCR layer
  is BZZ-compressed — a Burrows-Wheeler transform under an adaptive binary
  arithmetic coder — and nothing on PyPI decodes it without the DjVuLibre C
  library and a compiler, which is exactly the dependency a project that must
  zip up and run on Windows cannot take. The 251-entry coder table is a format
  constant, not a tuning choice. Verified byte-for-byte against DjVuLibre's own
  `bzz` encoder, *including on incompressible input*, which is the case that
  drives the least-probable-symbol path compressible text never reaches.
  A DjVu with no OCR layer fails loudly rather than ingesting empty.
- **A book has no abstract, so the abstract is a five-rung ladder**
  (`metadata/synopsis.py`): labelled abstract → preface → unlabelled abstract
  by shape → introduction → a digest assembled from the document's own most
  topical paragraphs. The shape rung is **switched off above ~65 pages**: its
  rule is "the first substantial paragraph near the top is the abstract", which
  is true of a preprint and false of a book, where it returns chapter one.
  The digest is deliberately capped at abstract length — if books contributed
  four thousand characters and papers twelve hundred, that difference would
  land in the document vector and the map would start clustering by *format*.
- **Four things read as prose and are not**, all found on the real corpus,
  all rejected by `synopsis.is_furniture`: a copyright page (long, punctuated,
  not especially capitalised); a converted Markdown table, which is how a
  keywords table became a paper's abstract; a figure caption, which is often a
  paper's single most topical paragraph and is about the figure; and a
  paragraph beginning mid-sentence, which is the tail of one the converter
  split — the norm in OCR output. Boilerplate is matched only in a paragraph's
  *opening*: a real abstract that runs on into a page footer says "all rights
  reserved" a thousand characters in, and rejecting it for that cost two
  genuine abstracts before the rule was narrowed.
- **Chunk vectors were computed and discarded.** Chunks are retrieved by BM25
  over `chunks_fts`; nothing ever read a chunk vector. Invisible at 57 papers,
  not at 550 with books among them, where one book is hundreds of chunks. Only
  the document vector is embedded now.
- **Cleanup deletes.** Broken files and byte-identical duplicates are removed
  from disk; `--quarantine` moves them to `data/quarantine/<timestamp>/` with a
  manifest instead, and `--dry-run` is the right first run on an unfamiliar
  library. Only files *presenting themselves as documents* are ever candidates,
  so a cover image or a `.bib` filed alongside the papers is left alone.
- **Duplicate detection groups by size before hashing.** Byte-identical files
  are the same size, so a library of 550 distinct files hashes nothing at all.
- **Model caches must be configured before torch is imported, in every
  process.** The API loads the embedding model too — for search — and
  `encode_query` reached the loader directly, bypassing `configure_environment`.
  Nothing failed; the only symptom was 1.6 GB of weights in
  `~/.cache/huggingface` and a checkout that would have been hollow if zipped
  and moved. `scripts/check_portability.py` is the gate.
- **`os.kill(pid, 0)` is not a liveness probe on Windows.** Every signal value
  but the two console-control ones reaches TerminateProcess, so the portable-
  looking probe kills what it asks about. `app/cli/stop.py` queries instead.

## Layout

```
backend/app/
  core/      config, db (WAL pragmas), paths (traversal guard), gpu, events
  models/    paper · tagging · projection · system   (all re-exported in __init__)
  routers/   thin HTTP; no business logic
  services/  ingest · parse · metadata · embed · tagging · project
  workers/   queue (SQLite-backed) · runner · handlers
  cli/       console entry points (scinet-stop)
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

## M2 invariants

- **The map persists.** Vectors in a memmap, reducer + fit matrix on disk,
  coordinates in SQLite. Opening the app never refits.
- **Inserts never move existing nodes.** New papers go through `transform()`
  and are flagged `is_transformed`; a test asserts prior coordinates are
  byte-identical after an insert.
- **Every refit is Procrustes-aligned** onto the previous run before it becomes
  visible, and the swap is a single `is_active` flip.
- **Distances are computed in embedding space, never on x/y/z.** UMAP distorts
  global distance deliberately; `/api/graph/similar` and any threshold filter
  must use the 1024-D vectors.
- **Cluster on the 10-D UMAP**, not the 3-D display coordinates.
- **The tag vocabulary is closed.** Proposals merge by label-embedding
  similarity or wait in `pending_review`; they never join silently.
- **Free the card between stages.** `free_all_models()` releases all three
  runtimes (torch cache, spawned llama-server, private Ollama). Tier 1's server
  is a *child process* — clearing the Python cache frees almost nothing.

## Process boundaries

- **The worker owns the GPU; the API must not touch it.** The single residency
  slot is per-process, so the API has no visibility into it. Semantic search
  embeds queries on the **CPU** for exactly this reason — it returned
  `CUDA out of memory` to the search box otherwise.
- **The embedding model warms on a background thread at API startup.** Loading
  it takes ~26 s; startup answers in ~0.6 s. Until it is ready, semantic search
  returns 503 with `{"state": "warming"}` and the UI waits and retries. That is
  a distinct answer from `failed` and from an empty result.
- **Surya spawns servers the default manager does not own** (`surya.ocr_error`
  among them). `free_all_models()` scans `/proc` for them; one was found alive
  hours after its worker exited, holding 800 MiB.

## Measured numbers (not estimates)

- tier-0 probe: **~2 ms/page**; tier-0 Markdown conversion: **~225 ms/page**.
  The 100x gap is why the router defers conversion until a tier has won.
- `pymupdf4llm.to_markdown` must be called with `use_ocr=False`. Left on, it
  runs Tesseract inside the "cheap CPU tier", costs ~29% more, and destroys the
  meaning of the quality gate.
- Browser WebGL runs on the **Intel Arc iGPU**, not the RTX 4060, under hybrid
  graphics. Good: the UI never competes with the worker for VRAM.
- 4,000 nodes render at a p95 frame of ~17 ms (vsync-locked, no drops); the
  real 293-node corpus measures p95 17.3 ms at 1600x808.
- **GPU picking is a synchronous readback and costs a frame.** With it live
  during an orbit: p95 25.7 ms / 50 fps. Suppressed while the camera moves:
  17.3 ms / 60 fps. Picking resumes ~90 ms after the camera settles.
- Real corpus tier split: **99.7% tier 0**, one paper escalating. Tier 1 saves
  3.6 minutes over 300 papers and is off by default (`SCINET_TIER1_ENABLED`).
- Semantic query: **26 s cold** (warmed in the background), **0.25 s warm**.
- Embeddings: Qwen3-Embedding-0.6B, 1024-dim, **1154 MiB peak GPU**. Sanity
  check on cosine similarity: related 0.883 > unrelated 0.540 > very unrelated
  0.424.
- Measured resident footprints: `granite3.2-vision:2b` 3.52 GiB,
  `qwen3:8b` 5.54 GiB, both fully GPU-resident. Rejected:
  `qwen2.5vl:7b` 13.3 GiB and `qwen2.5vl:3b` 10.05 GiB — both CPU-only.

## Commands

```bash
cd backend && uv run pytest                     # tests
cd backend && uv run python -m app.workers.runner   # worker
cd backend && uv run python ../scripts/backfill.py  # bulk import
cd backend && uv run python ../scripts/clean_library.py --dry-run  # find junk
cd backend && uv run python ../scripts/check_portability.py  # models stay local
cd backend && uv run python ../scripts/fix_abstracts.py  # re-derive bad abstracts
cd backend && uv run scinet-stop                 # stop API, worker, Vite
./scripts/stop.sh                                # the same, from anywhere
cd frontend && bun test                          # frontend unit tests
bun scripts/verify_render.mjs http://localhost:5173  # 3D render gate
cd backend && uv run python ../scripts/eval_clustering.py  # cluster quality
cd backend && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts
cd backend && uv run alembic revision --autogenerate -m "..."
cd backend && uv run alembic upgrade head
cd frontend && bun run typecheck
```
