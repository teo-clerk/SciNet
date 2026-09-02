# SciNet

[![ci](https://github.com/teo-clerk/SciNet/actions/workflows/ci.yml/badge.svg)](https://github.com/teo-clerk/SciNet/actions/workflows/ci.yml)

A local, privacy-first map of your scientific paper library.

SciNet watches a folder of PDFs, parses them to Markdown with local models,
embeds and tags them with local LLMs, and renders the whole corpus as an
interactive 3D semantic map. Nothing leaves the machine unless you explicitly
turn enrichment on.

## Status

Working end to end on a real 300-paper library. Papers are watched or uploaded,
deduplicated, parsed, embedded, positioned in 3D, clustered, named and tagged;
the map persists and updates incrementally, and renders at 60 fps.

The full suite — 650+ backend tests and 86 frontend tests — runs in CI on
every push, on a runner with no GPU, no model weights, and no network access
to models: the pipeline's seams are designed to be testable without the
hardware they orchestrate.

![The time scrubber replaying a 506-document library year by year — press
play and fields fade up as their years arrive](docs/media/time-scrubber.gif)

Because every refit is Procrustes-aligned and old runs are kept, the map can
hold **two embedding models' opinions of the same library** and morph between
them — the nodes that travel farthest are the papers the models disagree
about:

![Morphing 506 papers between SciNCL's and SPECTER2's layouts — the chrome
steps aside and the disagreement moves](docs/media/embedding-morph.gif)

```bash
cd backend && uv run python ../scripts/build_alt_projection.py allenai/specter2_base --apply
```

And the library can be *asked*. The librarian is a local agent whose tool
calls are map actions: it plans with constrained decoding, searches the
library, then streams an answer whose citations are validated in flight
against the evidence its tools actually returned — a citation the retrieval
never saw is stripped mid-stream, visibly. The camera flies to the evidence,
cited papers pulse, and a trail walks them in order. All of it local.

![The librarian answering from a 506-document library — planned searches,
a streamed cited answer, and the map flying to the evidence](docs/media/librarian.gif)

Every measured value in the library is a queryable row: numbers next to
units the extractor *recognises* become SI-normalised quantities carrying
the sentence they came from, and the filter bar turns a physical range —
1–100 Hz, say — into geometry. Extraction is allowlist-strict because the
first live run proved why: on a biology corpus, bare "A" matched matrix
indices a thousand times and amperes never. Tokens that only look like
units go to a local model, and its doubt lands in a review queue — nothing
enters the filters on a guess.

![Filtering 506 papers to those reporting 1–100 Hz — the oscillation
literature lights up, a paper's card lists its measured values, and the
review queue holds what the adjudicator was unsure about](docs/media/quantities.gif)

**[USAGE.md](USAGE.md) is the guide** — setup, the three commands to run it,
how to get papers in, and how to read the map.

Measured on 300 arXiv papers across 10 fields:

| | |
|---|---|
| tier-0 parse (99.7% of papers) | ~390 ms/page |
| whole-corpus ingest | ~50 min, tagging-dominated |
| map render, 293 nodes | 60 fps, p95 17.3 ms |
| semantic query | 0.25 s warm |
| clustering vs known fields | ARI 0.697, 10 regions, 1 unclustered |
| embedder A/B, 506 papers (SciNCL vs SPECTER2) | mean node shift 1.90 of radius 40 |
| quantity extraction, 506 papers | 16,275 rows in 6 s, CPU only |
| extraction precision, labelled fixture | 25/25 auto emissions correct (CI floor 0.90) |
| abstract coverage | 287 of 293 |

## How the map stays fast

Once computed, the map is *kept*. Nothing is recomputed on open:

| what | where |
|---|---|
| document vectors | `data/vectors/doc_vectors.f32` (growable memmap) |
| fitted reducer + its fit matrix | `data/models/projections/run_NNNNN.*` |
| coordinates, clusters, tags | SQLite |

Opening the app issues one `GET /api/graph`, which returns a 304 when the map
has not changed. Positions travel as a raw `Float32Array` — 48 KB for 4,000
nodes, against roughly 20 MB of equivalent JSON.

Dropping new PDFs into the library does **not** rebuild anything. Each is
embedded and placed with `reducer.transform()` against the stored fit, in
milliseconds, and marked *provisional* so the UI can show it was positioned
without a refit. A full refit happens only when either:

- more than 20% of the corpus was placed incrementally, or
- 25 or more papers sit measurably off the fitted manifold (you started reading
  a new field).

When a refit does happen it is **Procrustes-aligned** onto the previous layout
before anyone sees it. UMAP's orientation is arbitrary — two fits of nearly the
same data come out rotated, reflected and rescaled — so without alignment every
refit teleports every node and destroys the spatial memory you have built of
your own library. Alignment reduces mean node movement by more than tenfold, so
the map *settles* instead of scrambling.

The swap is atomic: a refit is computed into a new `projection_runs` row and
becomes visible only when `is_active` moves. A crashed refit leaves the old map
untouched, and the previous run stays on disk for rollback or for A/B-ing two
embedding models.

## Requirements

- Python **3.12** (pinned — `umap-learn` is not tested above it)
- Node 20+ / bun
- The [Ollama](https://ollama.com) **binary** (SciNet runs its own instance;
  it does not use your system-wide models)
- NVIDIA GPU recommended (8 GB is enough; models load one at a time)
- ~11 GB of disk for models

## Models

SciNet ships its own models. It does **not** use whatever happens to be
installed in your system-wide Ollama — a global model may be absent, a
different quantization, or silently updated, and none of that should decide
whether this project works.

Everything lives under `data/models/` (configurable via `SCINET_MODELS_DIR`):

```
data/models/
  ollama/   private model store; served by SciNet's own Ollama on port 11500
  hf/       HF_HOME for torch/transformers weights (Marker, embeddings)
```

Provision them once:

```bash
cd backend
uv run python ../scripts/download_models.py            # download what is missing
uv run python ../scripts/download_models.py --measure  # download, then measure VRAM
uv run python ../scripts/download_models.py --check    # report only
```

### Why footprints are measured, not estimated

Download size does not predict resident footprint, and being wrong is
expensive:

| model | disk | measured resident | on GPU |
|---|---|---|---|
| `qwen2.5vl:7b` | 5.56 GiB | **13.3 GiB** | 0% → 181 s/page |
| `qwen2.5vl:3b` | 2.98 GiB | **10.05 GiB** | 0% |
| `granite3.2-vision:2b` | 2.27 GiB | **3.52 GiB** | 100% → 8.9 s/page |

Ollama does not refuse to load an oversized model. It silently serves it from
system RAM, roughly twenty times slower, and the first symptom is a backfill
that looks hung. Note the 3B: a 2.98 GiB download with a 10 GiB footprint —
the Qwen2.5-VL dynamic-resolution vision tower carries a ~3.4x activation
budget, so neither parameter count nor download size predicts anything.

So `app/core/models_registry.py` records a *measured* `vram_mib` per model, an
unmeasured model is treated as **unproven** rather than assumed to fit, and
`scripts/doctor.py` reports the situation before you start a long job.
Full detail in [docs/MODELS.md](docs/MODELS.md).

### Before a long run

```bash
cd backend
uv run python ../scripts/doctor.py             # will anything run on CPU?
uv run python ../scripts/survey_corpus.py ~/Papers   # how much will escalate?
uv run python ../scripts/bench_parse.py --tiers 0,1  # what does a page cost?
```

`survey_corpus.py` probes the text layer of every PDF at ~6 ms each without
touching the GPU, and reports what fraction would escalate past tier 0 and why.
That turns "is tier 1 worth it for my library?" into a number.

## Setup

```bash
cp .env.example .env          # review the paths and the privacy switch

cd backend
uv sync --group dev           # core stack
uv run alembic upgrade head   # create data/scinet.db

cd ../frontend
bun install
```

The heavy GPU stack (torch, sentence-transformers, marker-pdf) is a separate
extra so the first install stays small:

```bash
cd backend && uv sync --group dev
```

## Running

Three processes:

```bash
# terminal 1 — API (127.0.0.1 only)
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# terminal 2 — worker (parsing, and later embedding and tagging)
cd backend && uv run python -m app.workers.runner

# terminal 3 — UI
cd frontend && bun run dev
```

Open <http://localhost:5173>.

## Importing a library

Drop PDFs into `data/library/` and the watcher picks them up. To import an
existing collection in bulk:

```bash
cd backend && uv run python ../scripts/backfill.py ~/Papers
```

Backfill is resumable: registration is idempotent on the content hash and the
job queue is durable, so interrupting it and re-running picks up where it left
off.

## How parsing decides what to spend

Probing a PDF's text layer costs ~2 ms/page. Converting it to Markdown costs
~225 ms/page (measured, single-threaded, on an Intel Ultra 9 185H). The gate in
`services/parse/quality.py` uses the cheap probe to decide the tier, so the
expensive conversion only runs on output that will actually be kept.

| Tier | Engine | Cost | Handles |
|---|---|---|---|
| 0 | PyMuPDF text layer | ~225 ms/page, CPU | most publisher and arXiv PDFs |
| 1 | Marker + Surya | ~1-3 s/page, GPU | broken layouts, scans |
| 2 | `qwen2.5vl:7b` | ~10 s/page, GPU | what neither of the above can read |

Tier 1 needs `uv sync --extra tier1`. Without it the router falls through to
tier 2 rather than stranding the paper.

## Privacy

All parsing, embedding, and tagging run locally, always. `SCINET_ENRICHMENT_ENABLED`
is the single switch that permits outbound calls to Crossref / OpenAlex / arXiv
for bibliographic cleanup. It defaults to `false`, and every call made while it
is on is recorded in the `egress_log` table:

```bash
sqlite3 data/scinet.db "SELECT ts, service, url FROM egress_log ORDER BY ts DESC LIMIT 20"
```

## Use it from your AI tools

SciNet is an MCP server: any Model Context Protocol client — Claude Code,
Claude Desktop, an IDE — can search your library, read your papers, and walk
the map's regions while you work.

```bash
claude mcp add scinet -- uv --directory /path/to/SciNet/backend run scinet-mcp
```

Read-only, localhost-only, and the API must be running (`uv run scinet-up`).
Details and the full tool table: [docs/MCP.md](docs/MCP.md).

## Health checks

```bash
sqlite3 data/scinet.db "SELECT status, count(*) FROM papers GROUP BY status"
sqlite3 data/scinet.db "SELECT kind, state, count(*) FROM jobs GROUP BY 1,2"
```

## Architecture

Two processes, one SQLite file. The worker is the sole writer of paper data;
the API reads and enqueues. See `docs/` and the design plan for the full
rationale, including why the job queue is not Celery and why the map uses UMAP
coordinates rather than a force-directed layout.
