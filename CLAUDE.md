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
- **Every projection parameter is a function of N** (`project/scaling.py`).
  The neighbourhood law is `min(N/8, sqrt(N))`: the two halves cross at 64, so
  it reproduces the hand-measured small-corpus rule below that and scales as
  the square root above. Fixed at 15 it was a quarter of a 60-paper library and
  a thirtieth of a 500-paper one — a map that smooths over a fixed *number* of
  papers smooths over a shrinking *fraction* as the library grows, which is how
  506 nodes became one cloud. Floors, `min_samples` and the dominant-cluster
  share scale the same way. Square root, not linear: a library ten times larger
  does not have ten times as many meaningful regions.
- **`should_refit` is evaluated again after incremental placement.** Taken only
  beforehand it cannot see the thing it exists to detect — placing the new
  papers is what makes the fit stale. And nothing comes back to look: `enqueue`
  collapses duplicate corpus-wide jobs, so one import produces exactly one
  projection job. A 57-paper fit absorbed 449 documents in a single pass (89%
  against a 20% trigger) and stayed active, leaving 449 of 506 unclustered.
- **The map's chrome is rationed by the viewport, not the corpus**
  (`frontend/src/lib/density.ts`). A screen holds ~12 readable labels however
  many nodes are beneath them; 40 names over 40 regions is a wall of text with
  the map behind it. Labels go to the largest regions, bridges to the strongest
  — faded rather than cut, so a weak relationship reads as weak instead of as a
  missing one. Nothing is lost: clicking still names any region, and the
  inspector lists every bridge.
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
- **Only the first 80 pages of any document are read** (`max_parse_pages`,
  `parse/limits.py`). Chosen from the shape of a real library, not a token
  budget: 465 documents, median 24 pages, longest 1,015, and *almost nothing
  between 60 and 150* — raising the cut from 60 to 150 changes how many
  documents are affected by thirteen. Below the gap are papers, read whole;
  above it are books, where page 400 says nothing new about what the book is.
  The stated reason for the limit was context-window overflow, and that part
  was not real — `document_text` caps at 6,000 chars and the tagger's prompt at
  6,000 against an 8,192 context. The real reason is compute: tier 2 is
  8-15 s/page, and one 731-page scan was killed after seven hours, unfinished,
  with 435 documents queued behind it. The same book now takes 39 s.
- **A truncated document still records its true page count.** Only the Markdown
  is short, and it carries a note saying so — every later stage reads the file,
  not the row. `synopsis` needs that note: it decides a document is a book by
  *length*, and truncation is precisely the operation that makes a book short,
  so a 700-page scan cut to 80 pages of sparse OCR would come back under the
  threshold and be read as a preprint.
- **Per-page metrics divide by pages sampled, not by page count.** Introduced
  by the page limit itself: the probe reads 80 pages of a 731-page book but
  reports 731, so `chars_per_page` came out a ninth of the truth and the book
  failed as "insufficient text".
- **`page_is_image` needs thin text as well as a big image.** A scanned book
  someone already OCR'd is every-page-image *and* perfectly readable; 12% of
  sampled PDFs — all books — were failing on the ratio alone while yielding
  300-2,900 chars/page, and each was being sent to the VLM to reproduce text
  that was already correct. Narrowing it took tier-0 acceptance from 83% to
  98% on the real library. A genuine scan still fails, on five counts.
- **Chunk vectors were computed and discarded.** Chunks are retrieved by BM25
  over `chunks_fts`; nothing ever read a chunk vector. Invisible at 57 papers,
  not at 550 with books among them, where one book is hundreds of chunks. Only
  the document vector is embedded now.
- **The embedder is a hard dependency, not an extra.** It lived under
  `--extra gpu` because it drags torch in (~2.5 GB), which is a real cost and
  the wrong thing to make optional: the document vector is what places a paper
  on the map, so a base install parsed 506 documents and then failed 449 embed
  jobs in a row with `ModuleNotFoundError`. `marker-pdf` stays optional under
  `--extra tier1` — nothing stops working without it, the router falls through.
- **`dead` is a verdict on the document, not on the machine.** A stage that
  fails because a library is missing burns all three attempts on the same
  ImportError, and nothing ever retries it — being out of retries is what
  `dead` records. `scripts/revive_jobs.py` returns only jobs whose recorded
  error matches a known environmental cause, and checks the module imports
  before doing it; a DRM-locked book stays dead.
- **Retrying is for a bad minute, not a bad file.** `parse/errors.py` splits
  failures in two: a property of the *document* (encryption, no text layer, a
  container that will not open) fails its job immediately; a property of the
  *moment* (a timed-out call, an unreachable model server) keeps all three
  attempts. This is not just wasted work — `fail()` requeues and `claim_next`
  orders by id, so a retried document is re-claimed *ahead* of the one behind
  it and its neighbour waits too. The classification is deliberately
  conservative: anything unrecognised is transient, because retrying a broken
  file wastes minutes while giving up on a good one loses it until somebody
  notices.
- **A file the parser gives up on leaves the library** (`ingest/quarantine.py`),
  and the Paper row follows it — otherwise every later "has this disappeared?"
  check reports a file sitting safely in quarantine. Only the *parse* stage
  quarantines: a paper that parsed and then failed to embed is a good document
  whose model was busy, and moving it would fix a problem it does not have.
  Filed by day rather than by run, since a batch import trickles failures over
  hours. Reversible from the UI, because this acts on one parser's verdict —
  a much weaker claim than the content checks in `cleanup` make.
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
- **Tier 0 counts vector paths before it lays a page out** (`MAX_PAGE_PATHS`,
  `parse/limits.py`). Its cost model assumed a page costs its text layer; the
  layout engine also walks every path, and on the aerospace corpus one figure
  page — a scatter plot drawn point by point, 1.3 million paths — held the
  worker for 5.5 hours with 48 documents behind it. pymupdf4llm's
  `graphics_limit` is silently dropped on the layout-engine path, so the gate
  is ours: over 20,000 paths (every stalled page; the median busiest page has
  78) the page is read as plain text, which keeps its prose and loses only
  the layout, and the Markdown names the pages.
- **The demo corpus is a benchmark, and it is separate.** `demo/manifest.jsonl`
  pins 85 open documents named `Domain_identifier.pdf`; `fetch_demo_corpus.py`
  rebuilds it from the sources with hash checks and is user-invoked, not app
  egress. It lives under `data/demo/` beside the personal library:
  `SCINET_DATA_DIR` relocates the library, Markdown, vectors and database
  together, and deliberately not the models. Nine wrong titles on that corpus
  were worth 0.05 ARI and an entire region.
- **The semantic-search limit scales with the library** (`semanticLimit`,
  `frontend/src/lib/search.ts`): k nearest is the whole answer to "how much
  lights up", and a fixed 60 lit 70% of an 85-paper map. Square root, like
  the projection's own parameters.
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
  cli/       console entry points (scinet-up, scinet-stop)
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
cd backend && uv run python ../scripts/fetch_demo_corpus.py --dest ../data/demo/library  # the benchmark corpus
cd backend && SCINET_DATA_DIR=data/demo uv run scinet-up  # run it beside the personal library
cd backend && uv run python ../scripts/clean_library.py --dry-run  # find junk
cd backend && uv run python ../scripts/check_portability.py  # models stay local
cd backend && uv run python ../scripts/fix_abstracts.py  # re-derive bad abstracts
cd backend && uv run python ../scripts/revive_jobs.py  # requeue env-killed jobs
cd backend && uv run python ../scripts/extract_quantities.py --apply  # re-extract measured values
cd backend && uv run python ../scripts/force_project.py --apply  # rebuild the map
cd backend && uv run python ../scripts/prepare_export.py  # clean before zipping
cd backend && SCINET_MAX_PARSE_PAGES=0 uv run python -m app.workers.runner  # no page cap
cd backend && uv run scinet-up                   # API + worker + UI, one terminal
cd backend && uv run scinet-mcp                  # MCP server (stdio; needs the API up)
cd backend && uv run scinet-stop                 # stop API, worker, Vite
./scripts/stop.sh                                # the same, from anywhere
bun scripts/record_demo.mjs scripts/scenarios/smoke.mjs  # record a scripted demo
cd frontend && bun test                          # frontend unit tests
bun scripts/verify_render.mjs http://localhost:5173  # 3D render gate
cd backend && uv run python ../scripts/eval_clustering.py  # cluster quality
cd backend && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts
cd backend && uv run alembic revision --autogenerate -m "..."
cd backend && uv run alembic upgrade head
cd frontend && bun run typecheck
```
