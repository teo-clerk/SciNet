# How SciNet works — the parts that are measured

The README says what SciNet is for. This page is the engineering behind it:
why the map keeps its shape, how parsing decides what a page is worth, which
models fit in 8 GB and how that was found out, and the numbers the whole
thing was measured against. Every figure here was measured on the hardware
named, not estimated.

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

Because every refit is aligned and old runs are kept, the map can hold **two
embedding models' opinions of the same library** and morph between them — the
nodes that travel farthest are the papers the models disagree about:

![Morphing 506 papers between SciNCL's and SPECTER2's layouts — the chrome steps aside and the disagreement moves](media/embedding-morph.gif)

```bash
cd backend && uv run python ../scripts/build_alt_projection.py allenai/specter2_base --apply
```

## How parsing decides what to spend

Probing a PDF's text layer costs ~2 ms/page. Converting it to Markdown costs
~225 ms/page (measured, single-threaded, on an Intel Ultra 9 185H). The gate in
`services/parse/quality.py` uses the cheap probe to decide the tier, so the
expensive conversion only runs on output that will actually be kept.

| Tier | Engine | Cost | Handles |
|---|---|---|---|
| 0 | PyMuPDF text layer | ~225 ms/page, CPU | most publisher and arXiv PDFs |
| 1 | Marker + Surya | ~1-3 s/page, GPU | broken layouts, scans |
| 2 | vision model (`granite3.2-vision:2b`) | ~9 s/page, GPU | what neither of the above can read |

Tier 1 needs `uv sync --extra tier1`. Without it the router falls through to
tier 2 rather than stranding the paper.

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
Full detail in [MODELS.md](MODELS.md).

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

## The benchmark

`demo/manifest.jsonl` pins 85 open documents across five aerospace subfields
— radar imaging, machine learning on satellite imagery, trajectory
optimisation, astronomical instrumentation, satellite positioning — seventeen
arXiv preprints and two public-domain NASA technical reports in each, 1963 to
2026, filed as `Domain_identifier.pdf` so the map can be scored against the
truth. Nothing is redistributed; one command fetches the corpus from its
sources and checks every hash, and the score is reproducible on your machine:

```bash
cd backend
uv run python ../scripts/fetch_demo_corpus.py   # 85 documents, ~5 minutes
uv run alembic upgrade head
uv run python ../scripts/backfill.py
uv run scinet-up                                # then, once the map exists:
uv run python ../scripts/eval_clustering.py
```

Five fields, four regions. Radar imaging and optical Earth observation share
one — on arXiv, radar imaging *is* machine learning on satellite imagery now
— and the other three come out pure: adjusted Rand index **0.725**, nothing
left unclustered.

The corpus found things, which is what a benchmark is for. The two genuine
scans went through the vision model as intended. One 13-page born-digital
paper held the parser for five and a half hours: a scatter plot drawn point
by point, 1.3 million vector paths, which the layout engine walks one by one
— tier 0 now counts paths first and reads such a page as plain text in
seconds. And nine of the 85 titles were wrong (Word templates left in the
PDF's Title field, author bylines the converter set as headings); fixing
them was worth 0.05 of ARI and an entire region — the trajectory papers,
unclustered noise before, are a pure region after.

Measured on the 85-document aerospace benchmark:

| | |
|---|---|
| clustering vs the five true fields | ARI 0.725 · homogeneity 0.781 · completeness 0.946 · 4 regions, 0 unclustered |
| the same corpus before nine titles were fixed | ARI 0.675 · 3 regions, 18% unclustered |
| tier-0 parse, 85 documents, 1,877 pages | median 15 s per document; 73 of 85 under a minute |
| tier-2 OCR, the two scans (18 and 20 pages) | 785 s and 882 s, three page timeouts each |
| the paper with 1.3 million vector paths | 19,825 s before the path gate, 20 s after |
| embedding, 85 documents | 18 s |
| refit + cluster + name + bridges | 47 s |
| quantity extraction + adjudication | 2,572 automatic rows over 84 of 85 papers; 18 left for review |
| outbound requests, whole pipeline | `SELECT count(*) FROM egress_log` → 0 |

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

The full suite — 900+ backend tests and 230+ frontend tests — runs in CI on
every push, on a runner with no GPU, no model weights, and no network access
to models: the pipeline's seams are designed to be testable without the
hardware they orchestrate.

## Health checks

```bash
sqlite3 data/scinet.db "SELECT status, count(*) FROM papers GROUP BY status"
sqlite3 data/scinet.db "SELECT kind, state, count(*) FROM jobs GROUP BY 1,2"
```

## Architecture

Two processes, one SQLite file. The worker is the sole writer of paper data —
it also watches the library folder — and the API reads and enqueues. The MCP
server is a third, short-lived process the MCP client spawns; it proxies the
API and never opens the database. The job queue is not Celery because one
SQLite file is the whole deployment; the map uses UMAP coordinates rather than
a force-directed layout because positions that mean something cannot be
allowed to drift every frame. The rest of the reasoning lives in the module
docstrings, which are the design document.
