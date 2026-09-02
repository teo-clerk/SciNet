# SciNet — Execution Plan: MCP Server · Model Lab Router · Librarian · Quantities

**Approved:** 2026-09-02. This is the working plan of record for the post-audit feature push; `PROJECT_AUDIT.md` holds the stabilization findings it builds on, `EXPANSION_IDEAS.md` the wider option space it was chosen from.

## Context

SciNet's audit stabilized the repo; the expansion brainstorm mapped what to build next. The choice: skip minor UI polish and attack the four highest-wow, CV-grade features, with scope confirmed directly:

1. **Dynamic AI Router & Recommender** at **"Model Lab" depth** — hardware/VRAM detection, a per-machine model-profile catalog, discovery of installed Ollama models, one-click measurement/benchmarks run as queue jobs, per-task routing with user pins, and a Model Lab UI.
2. **MCP Server** — the library as context for any MCP client (Claude Code/Desktop, etc.).
3. **The Librarian that Flies the Map** — a local agent whose tool calls are camera flights, node highlights, and trails, with citation-gated answers streamed over SSE.
4. **Quantities Extraction** at **regex+pint core + LLM adjudication + review queue** depth (results-tables meta-analysis deferred).

**Build order: MCP → Model Lab → Librarian → Quantities.** Each feature lands usable before the next starts. The Librarian depends on the Model Lab's routing + pause lease; Quantities is independent and last.

**Seven approved add-on initiatives** are woven in without changing the four features: **Phase 0** (CI + `scinet up` supervisor + demo-capture harness) before feature work; a **time scrubber** mini-phase after MCP; the **embedding A/B morph view** as Phase 2b (Model Lab's visual payoff); a **pipeline pause button + live VRAM gauge** riding Phases 2–3; a **quantities scatter panel** as a Phase 4 stretch; and a parallel **aerospace demo-corpus track** so every phase demos against recruiter-relevant content. Final sequence: **Phase 0 → 1 (MCP) → 1b (scrubber) → 2 (Model Lab) → 2b (morph) → 3 (Librarian) → 4 (Quantities)**, with Track C (corpus) running alongside from Phase 1.

Everything below is grounded in a three-agent exploration of exact seams (signatures, file:line) plus a design-validation pass; the audit's findings are absorbed where a feature touches them (broken `doctor.py` call, hardcoded `TOTAL_VRAM_MIB`, `Vulkan1` defaults, `free_all_models` hardcoded model tuple, the `ollama_url_override` split-brain).

### Branch & commit strategy

- One feature branch per phase off `main`: `feat/phase0-infra`, `feat/mcp-server`, `feat/time-scrubber`, `feat/model-lab`, `feat/morph-view`, `feat/librarian`, `feat/quantities`; merged sequentially (keeps the alembic chain linear — revision ids assigned per phase below).
- House conventional commits ("feat(scope): what, and why it matters" voice). Every commit green: `uv run pytest` + `uv run ruff check . ../scripts && uv run ruff format --check . ../scripts` + `bun run typecheck` + `bun test`.
- All new code follows house style: module docstrings that argue the why, files 200–400 lines, frozen dataclasses for values, no GPU/network/model loads in tests (duck-typed Ollama clients via `client=` kwargs; handlers via `monkeypatch.setitem(HANDLERS, ...)`).

### Invariants that bind all four features

- **Local-only.** No new outbound calls; anything beyond localhost would have to go through the egress gate — none of these features needs it.
- **The worker owns GPU orchestration; the API may call Ollama over HTTP** (Ollama is its own process). `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1` serialize worst cases.
- **API↔worker cross-process state goes through SQLite** (the in-process `EventBroker` does not cross; `events.py:8-9`). The API already writes the jobs table (`graph.py:184`), so API-written system rows (settings/jobs) are precedented; paper data stays worker-only.
- New SSE event kinds must be added to the frontend `WATCHED` allowlist (`frontend/src/api/sse.ts:16-21`) or they are silently dropped.
- New env-settable fields: add to `core/config.py` AND `.env.example` (audit flagged drift here — don't repeat it).

---

## Phase 0 — CI, one-command start, and the demo harness (`feat/phase0-infra`, ~2-3 days)

**Goal:** every subsequent commit is CI-verified, every demo starts with one command, and every phase can record its own GIF deterministically.

- **CI:** `.github/workflows/ci.yml` — Linux runner: `uv sync --group dev` → `uv run ruff check . ../scripts` + `ruff format --check` → `uv run pytest` → `bun install` → `bun run typecheck` → `bun test`. The suite is verified GPU-free/network-free (audit), so this is green on day one; the one threaded queue test (`test_queue.py` 6-thread contention) is the only flake candidate — if it flakes under a loaded runner, relax its timing, never its assertion. Badges into README.
- **`scinet up`:** a supervisor console script (`app/cli/up.py`, sibling of `app/cli/stop.py`, entry `scinet-up`) that starts API (uvicorn), worker, and frontend (`bun run dev`), multiplexes prefixed log lines, prints the URL, and delegates shutdown to the existing `scinet-stop` machinery (which already knows how to find these processes without killing its own terminal). Windows-aware the way `stop.py` already is.
- **Demo capture harness:** `scripts/record_demo.mjs` — reuses the hand-rolled Bun+CDP pattern from `verify_*.mjs` (own debugging port, own `/tmp` profile, screenshot plumbing) plus CDP screencast frames → ffmpeg (if present) → MP4/GIF; takes a scenario module (a list of timed actions: navigate, hover, click, drag-orbit, type) so each phase adds a scenario file rather than a new harness. Deterministic: fixed viewport, waits on selectors not sleeps (the `verify_render.mjs` polling style).

**Commits:** 1. `ci: the suite has been green for weeks — make it prove it` (workflow + badges + README note). 2. `feat(cli): scinet up — the three terminals become one` (+ tests in the `test_stop.py` style: process matching, dry-run). 3. `feat(demo): a scripted camera for recorded demos` (harness + one smoke scenario against the current map; gated manually, not in CI).

**Verification:** first push shows green Actions; `uv run scinet-up` serves the map; `bun scripts/record_demo.mjs scripts/scenarios/smoke.mjs` writes an MP4.

---

## Phase 1 — MCP Server (`feat/mcp-server`)

**Goal:** `scinet-mcp` console script; any MCP client can search the library, read papers, and browse regions. Read-only v1, stdio transport, **proxy mode**: tools call the running API at `http://127.0.0.1:{port}` via httpx — reuses the API's warmed embedder; a down API returns a clear "start the API first" tool error.

**New files:** `backend/app/mcp/__init__.py`, `backend/app/mcp/server.py` (FastMCP app + tools), `backend/app/mcp/client.py` (thin httpx wrapper with the base-URL + error translation), `backend/tests/test_mcp_server.py`, `docs/MCP.md`.
**Touched:** `backend/pyproject.toml` (dep `mcp>=1.2,<2`; `[project.scripts] scinet-mcp = "app.mcp.server:main"`), README (a "Use it from Claude" section), USAGE.md.

**Tools** (names are the contract; keep outputs small and paginated):
`library_overview()` → counts, regions, top tags (wraps `/api/system` + `/api/clusters`); `search_library(query, mode="semantic"|"fulltext"|"title", limit)` (wraps `/api/search/*`; title mode = substring via `/api/papers?q=`); `similar_papers(paper_id, k)`; `get_paper(paper_id)` (metadata + abstract); `read_paper(paper_id, offset=0, window=4000)` (windows over `/api/papers/{id}/markdown` — protects client context); `list_regions()`; `region_details(cluster_id)`.
Semantic-search 503-warming becomes a friendly tool message with the ETA (same shape the UI shows — detail dict from `search.py:150-162`).

**Reuse:** endpoint inventory and response shapes from `routers/` (search.py:66/120, graph.py:197, papers.py:460, clusters.py:100/116, system.py:35). Base URL from `SCINET_API_URL` env or `Settings().port`.

**Testing:** FastMCP in-memory client; the httpx layer pointed at `httpx.ASGITransport(app=create_app())` with `dependency_overrides` (the `test_health.py:20-51` fixture shape) — full stack, no sockets, no models (semantic mode asserted via the warming path or a monkeypatched `encode_query`).

**Verification (end of phase):** `uv run scinet-mcp` handshakes over stdio; `claude mcp add scinet -- uv --directory backend run scinet-mcp` documented in docs/MCP.md and manually verified in Claude Code against the live library ("what do I have on X?").

---

## Phase 1b — Time scrubber (`feat/time-scrubber`, 1-2 commits, frontend-only)

**Goal:** a year slider + play button; nodes outside the cutoff ghost to the existing filtered look — scrub to watch fields emerge, press play for the fly-through of the corpus's history.

**Design:** zero renderer changes — a `yearCutoff: number | null` store field ANDed into `useVisibleSet` (`lib/filtering.ts`), exactly the mechanism the quantities filter will reuse in Phase 4 (this phase pioneers the pattern); dimming rides `aFiltered` as-is (future papers ghost at 0.08 rather than vanish — context, not deletion, the house filtering philosophy). Play = a `requestAnimationFrame` loop advancing the cutoff over ~8 s, in the FilterBar control, honoring the null-year convention (undated papers always visible, tested). Resets in `clearFilters` + `setGraph`.

**Commits:** 1. `feat(ui): scrub the library through time` (store field + filtering intersection + slider/play control + `filtering.test.ts` cases). 2. `docs(demo): the corpus history, recorded` (scrubber scenario for the Phase 0 harness; GIF into README).

---

## Phase 2 — Model Lab: Dynamic AI Router & Recommender (`feat/model-lab`)

**Goal:** SciNet knows the machine it's on, knows every local model available to it (private store + system Ollama), measures rather than assumes, routes each task to the best proven model, and shows all of it in a Model Lab panel.

### Architecture

- **`core/hardware.py`** (new): `probe() -> HardwareReport` (frozen dataclass: `vram_total_mib | None`, `gpu_name`, `ram_mib`, `disk_free_gib`) via `nvidia-smi` subprocess (moves `scripts/doctor.py:25-38` into `app/`), `/proc/meminfo`/`os.sysconf`, `shutil.disk_usage`. Graceful `None`s on machines without NVIDIA. `total_vram_mib()`/`vram_budget_mib()` are `lru_cache`d functions with the `models_registry` constants as **fallback** — the constants stay importable (the `test_gpu_lifecycle.py:101-116` invariants assert against them). **`GpuSlot`'s probed default is lazy** (resolved on first `fits()`/`hold()`, never at import — `GPU = GpuSlot()` runs at module import and must not spawn a subprocess there; every existing GpuSlot test passes an explicit total, so the probe never fires under pytest). **Absorbs:** the doubly-broken `doctor.py:54` call (TypeError on `total_vram_mib=` vs `vram_budget_mib=`, plus the semantic bug of passing raw/zero VRAM with no safety margin).
- **`model_profiles` table** (migration `d4e5f6a7b8c9`, chained on `c3d4e5f6a7b8`): per-machine measured facts — `reference` (unique), `runtime`, `role`, `disk_mib`, `vram_mib` (nullable = unproven), `tok_per_s`, `bench_json` (per-task scores), `verdict` ("gpu"/"partial"/"cpu-only"/"rejected"/"unproven"), `source` ("builtin"/"discovered"/"user"), `last_measured_at`, `notes`. **The static `REGISTRY` stays** as seed + known-good defaults (its invariant tests `test_gpu_lifecycle.py:101-120` untouched); `REJECTED_MODELS` (currently zero consumers) seeds `verdict="rejected"` rows. Measurement stops being "copy numbers back into the file by hand" (`download_models.py:248`).
- **`TaskKind` + router** (`core/model_router.py`): tasks finer than `Role` (which conflates tagging/naming/bridges into TAG): `TAG_PAPER`, `NAME_CLUSTER`, `DESCRIBE_CLUSTER`, `BRIDGE_VERDICT`, `PAGE_OCR`, `EMBED_DOCS`, `EMBED_QUERY`, `LIBRARIAN_CHAT`, `EXTRACT_ADJUDICATE`. `resolve(task, settings, session=None) -> str`; precedence: per-task pin (settings-table row `route.<task>`) → profile recommendation → legacy `settings.llm_model`/`vlm_model`/`embed_model` (full back-compat: with no pins and no profiles, behavior is byte-identical to today). `session=None` skips pin lookup — the router never opens its own DB session (that would leak the real database into tests). **Migration of call sites is deliberately partial in v1:** thread an optional `model: str | None` kwarg through the three seams that already hold a session (`handle_tag` → `tagger.tag_paper`, the projection pipeline → `naming.*`, `handle_parse` → tier-2), defaulting to the current settings read; the remaining sites keep reading settings defaults, which `resolve` falls back to anyway — identical behavior until someone pins, and a reviewable diff. **Embed routing is deliberately conservative:** changing the embed model invalidates vectors/projections (store is keyed by model slug, `embed_handlers.py:48-65`), so the router treats `EMBED_*` pins as "switch the whole store" with an explicit confirmation path, not a silent reroute.
- **Discovery:** list `/api/tags` on the private store AND a second `PrivateOllama(settings, port=11434)` probe of the system Ollama; `/api/show` for context length/quantization of discovered models. **Absorbs the split-brain bug:** availability checks and unloads route through the same base URL generation uses (`settings.ollama_url`), so `ollama_url_override` behaves consistently.
- **Measurement as queue jobs** (`JobKind.MEASURE`, priority **90** — after ENRICH, before ADJUDICATE/TAG; a measurement is a human waiting at the panel): VRAM residency via the `download_models.measure()` flow moved into `services/models/measure.py` (warm POST → `/api/ps` → classify, `download_models.py:120-151`; the script becomes a thin caller that persists instead of printing "update by hand"); tok/s from Ollama's `eval_count`/`eval_duration`. **Dedupe verdict:** a scoped `enqueue_measure(session, reference)` helper that payload-matches outstanding MEASURE jobs itself and inserts the `Job` row directly — `queue.enqueue` stays untouched, because making it payload-aware would invert the documented reproject-collapse guarantee (`graph.py:183`, pinned by `test_queue.py:210-234`). `max_attempts=2`; the handler unloads after itself (every MEASURE loads a different model; `drain_stage` only frees between kinds); a warm-generate timeout is recorded as a `cpu-only`-suspect verdict with a note, not a dead job. Embed models get footprint + CPU speed + dimension detection (wires the dead `encoder.embedding_dimension()`, `encoder.py:153`). **Stretch (last commit of the phase, defer freely):** the self-supervised OCR benchmark — render a known-text page (`tests/fixtures/generate.py` machinery + `tier2_vlm._render_page`) → VLM transcribes → char accuracy vs known text — and schema-compliance scoring; fit/speed is what gates everything on this hardware, quality ranking pays off only once several VLM candidates exist.
- **Recommender:** rank proven profiles per task under the probed VRAM headroom; everything unmeasured is "unproven — measure first" (house rule: never assume fit — Ollama silently serves oversized models from RAM at ~20×).
- **API + UI:** `routers/models.py` (`GET /api/models` hardware+profiles+discovery, `POST /api/models/measure`, `PUT/DELETE /api/models/pins/{task}`); frontend `panels/ModelLab.tsx` as a fourth `ViewMode` ("models") — hardware header with VRAM gauge + live residency readout, profile table with verdict badges + Measure buttons, per-task assignment dropdowns (proven models selectable, unproven greyed).
- **Also absorbed:** `free_all_models()` unloads whatever `/api/ps` reports resident instead of the hardcoded `(vlm_model, llm_model)` tuple (`gpu.py:146`); `SCINET_LLAMACPP_DEVICE` default → `""` (audit P0 item, config + .env.example + registry constant docs).

**Verification:** `scripts/doctor.py` works again and reports probed hardware; measuring a model from the UI produces a profile row and survives restart; pinning `TAG_PAPER` to another installed model visibly changes the model used on the next tag batch (log line); with no pins, all 634 existing tests still green (back-compat).

---

## Phase 2b — Embedding A/B morph view (`feat/morph-view`, 3 commits)

**Goal:** measure a second embedding model in the Model Lab, build a parallel projection under it, and morph the map between the two models' opinions with a slider — the nodes that travel farthest are exactly where the models disagree. The schema was built for this without knowing it: `DocVector` and `ProjectionRun` both carry `model_id`, the vector store is keyed by model slug (`embed_handlers.py:48-65`), inactive runs are retained on disk, and `procrustes.py` aligns any run onto any other.

**Design:** `scripts/build_alt_projection.py --model <ref>` (dry-run default): encode all docs with model B into its own store (`doc_vectors__<slug-b>`), fit a reducer, write an **inactive** `ProjectionRun(model_id=B)` Procrustes-aligned onto the active run — the active map never moves (the M2 invariant holds by construction). `GET /api/graph` gains an optional `run_id` query param (serves any retained run; default stays the active one — ETag already keys on run id). Frontend: fetch both runs' position buffers, lerp into the render attribute on slider change (in-place typed-array write, the house hot-path idiom), with a run picker listing retained runs by `model_id`. Slider lives in the Model Lab view — it is the Lab's payoff.

**Commits:** 1. `feat(project): a second opinion — alternate-model projection runs` (script + `run_id` param + tests: alt run is inactive, aligned, active map byte-identical). 2. `feat(ui): the morph slider between two embeddings` (dual-buffer fetch + lerp + picker; bun test the lerp math). 3. `docs(demo): watch two models disagree` (morph scenario GIF; measured mean-displacement number into README's table).

---

## Phase 3 — The Librarian that Flies the Map (`feat/librarian`)

**Goal:** ask the library a question; the answer streams in with citations, and the map flies, highlights, and draws the trail of the evidence.

### Architecture

- **Backend `services/librarian/`** (`schemas.py`, `tools.py`, `agent.py`): a bounded tool-loop over Ollama (model from `resolve(LIBRARIAN_CHAT)`). Tools are in-process calls to existing functions — semantic/fulltext search, `similar`, markdown windows (same process as the API; no HTTP hop); sync tool bodies run under `anyio.to_thread.run_sync`, and semantic tools are gated on `WARMER.is_ready` (emit a `status` frame and fall back to fulltext instead of blocking 26 s on a cold encoder). Loop: ≤3 non-streamed tool-selection rounds under constrained decoding (schema: `action` enum FIRST, then args — the house property-order rule, `naming.py:149-152`), then one **streamed** final answer. **Streaming and strict JSON schemas don't compose** (you can't stream readable prose out of a constrained JSON object), so the citation gate works in-stream instead: the answer streams as plain prose carrying inline `[#paper_id]` markers; the server validates each marker against the retrieved-evidence set as tokens pass through, strips the rest, and the `done` frame reports cited + dropped ids — hallucinated citations remain structurally impossible to render.
- **Endpoint** `POST /api/librarian/ask` → hand-rolled SSE `StreamingResponse` (frames: `status`, `tool_call`, `map_directive`, `answer_token`, `done`) copying the `routers/events.py:20-45` generator/heartbeat/headers pattern verbatim (do NOT introduce sse-starlette — a second framing convention for one endpoint); async endpoint (precedented — `stream_events` and `upload_papers` are already async) with `httpx.AsyncClient` streaming Ollama NDJSON (`stream: true` — the first streaming Ollama use). **Single-flight:** an in-process `asyncio.Lock` try-acquire; a second concurrent ask gets 409 "the librarian is already answering". History is client-sent and trimmed; no server sessions, no multi-turn memory beyond the current turn's evidence in v1.
- **Map directives:** the planner's output includes what to show — `fly_to` (paper/cluster), `highlight` (paper ids), `trail` (ordered ids; in the protocol union from day one, rendered later — see stretch). Server translates paper ids → directives; client translates ids → node indices.
- **GPU contention — the pause lease** (`workers/lease.py`, shared by both processes so serialization can't drift): settings-table row `worker.pause` = JSON `{"until": iso, "nonce": uuid, "reason": "librarian"}` written by the API; the worker checks it at the top of `drain_stage`'s claim loop (`runner.py:98`) and before each kind (`runner.py:80`), stops claiming while leased (returns 0; `run_forever`'s existing 2 s idle sleep paces the polling — no new loop), and acks via `worker.paused_ack` **echoing the nonce** — the API waits only for a *matching* nonce, so a stale ack from a previous turn can't satisfy the wait. Keep-warm rule is precise: skip `free_all_models()` **only when** `GPU.resident is not None and resident.runtime is OLLAMA and resident.reference == librarian_model` (compare `reference` strings, not `ModelEntry.key` — the key embeds the role and would never match); an EMBED- or tier-1-resident card frees, because qwen3:8b plus a resident encoder does not fit the budget. Lease TTL 180 s, refreshed from the token loop; the endpoint's `finally` releases it (`until = now`, best-effort — a crash falls back to expiry). Ack wait is bounded at ~15 s with `status` frames ("waiting for the worker to finish the current job"), then the librarian **proceeds without ack** with a distinct status ("worker busy; answers will be slow") — `OLLAMA_NUM_PARALLEL=1` serializes, so the worst case is bounded model-swap thrash, never corruption. Expired leases are ignored; no crash on either side can wedge the pipeline.
- **Frontend:**
  - `api/librarian.ts` — the repo's first fetch-streaming SSE parser (`res.body.getReader()` + `TextDecoder` + `\n\n` framing, `AbortController`; reuse `EngineWarming` from `lib/search.ts:64` for warm-up states). ~90 lines, pure-logic frame parser extracted for bun tests.
  - Store slices (flat-store conventions, resets wired into `setGraph`): `cameraRequest {target, distance, nonce}` (+ `flyToPoint` action), `highlightSet: Set<number> | null`, `trail: number[]`, chat state consumed **only** by the panel (selector discipline — chat tokens must never re-render the canvas).
  - `CameraRig.tsx`: one new `useEffect` on `cameraRequest` calling the existing generic `flyTo` (`CameraRig.tsx:45-65`); a `lib/framing.ts` helper computes centroid + viewing distance for "frame this set" (testable, modeled on `graphStore.centroidsOf`).
  - `PointCloud`: new `aHighlight` attribute + `uTime` uniform pulse — the exact 5 known edits (GraphBuffers/buildBuffers; attribute at `:40-60`; effect mirroring `:172-182`; vert varying; frag ring mirroring `:56-60`); pick shader untouched (undeclared attributes are inert).
  - `graph/Trail.tsx` **(stretch — last commit of the phase, defer freely):** drei `<Line dashed>` through `CatmullRomCurve3`-sampled node positions, `dashOffset` animated via ref in `useFrame` (BridgeCurves template; points memoized so the geometry never rebuilds per frame). `fly_to` + the highlight pulse already *is* "flies the map"; the client simply ignores `trail` directives until this lands, so shipping it later changes no protocol.
  - `panels/LibrarianPanel.tsx` — right-dock beside DetailPanel; citation chips use the `nodes.findIndex` + `setView('map')` + `setSelected` idiom (`ClusterInspector.tsx:45-52`). Keep highlight OUT of `gl_PointSize` so `pick.vert.glsl` (which mirrors the size formula) stays byte-identical.

**Verification:** with API+worker+UI running and the worker mid-batch, asking a question shows the "waiting for worker" status, then pauses the pipeline at the next job boundary, streams a cited answer, flies the camera, and pulses the cited nodes (plus the trail once the stretch commit lands); citations click through to papers; a question about absent content answers "not in your library" with zero fabricated citations (gate test); worker resumes within lease expiry. Backend loop + citation gate unit-tested with duck-typed clients; frame parser bun-tested; lease protocol tested by driving `Worker.drain_stage` with a lease row present (assert: no claim while leased, claims resume after expiry).

---

## Phase 4 — Quantities Extraction (`feat/quantities`)

**Goal:** every measured value in the library becomes a queryable row with provenance; the map filters by physical ranges; ambiguous hits go through LLM adjudication into a review queue.

### Architecture

- **`quantities` table** (migration chained after Phase 2's): `paper_id` FK CASCADE, `chunk_ord`, `section`, `quantity_kind` (String — e.g. length, altitude, resolution, isp, frequency, power, temperature, snr, data_rate), `value_si` Float + `unit_si`, `value_original` + `unit_original`, `context_sentence` Text (verbatim — chunks carry no char offsets), `confidence`, `extraction_source` ("regex"|"llm"), `status` ("auto"|"pending_review"|"confirmed"|"rejected"), `model_id`/`pipeline_version` provenance; indexes `(quantity_kind, value_si)` and `paper_id`.
- **Pipeline — two kinds, deliberately split** (a single kind doing regex+LLM at priority 35 would load qwen3 *ahead of PROJECT 40*, making the map wait on the LLM — against the codebase's central sequencing principle): `JobKind.EXTRACT` (priority 35, CPU-only, `STAGE_ORDER` picks it up automatically) enqueued per-paper from `handle_embed` once chunks are written (`embed_handlers.py:137` area). Handler = `workers/quantity_handlers.py` + `services/quantities/extract.py`: **delete-then-insert by paper_id** (re-parse safety — chunks are replaced wholesale, so stale rows would point at text that no longer exists; rows key on `(paper_id, chunk_ord)`, no chunk FK), sentence-split chunk text, precision-first regex (number+unit adjacency, ranges, ±, scientific notation) + a module-singleton `pint.UnitRegistry` (construction costs hundreds of ms) normalizing to SI; unambiguous → `status="auto"`; ambiguous → pending. It enqueues `ADJUDICATE` (corpus-wide, payload-free — standard collapse applies) **only when it wrote pending rows**, mirroring `handle_embed` → PROJECT. Backfill script `scripts/extract_quantities.py` (`--dry-run` default, house style).
- **LLM adjudication:** `JobKind.ADJUDICATE` (priority 95 — batches next to TAG 100 so it shares the LLM-resident era) drains pending rows in **batches of ~25 with a commit per batch** (one failure must not re-adjudicate hundreds of LLM calls; mid-handler commits are precedented, `handlers.py:87` — a crash resumes free because pending rows are durable). Constrained decoding schema: `quantity_kind` enum FIRST, then normalized fields, prose last; model via `resolve(EXTRACT_ADJUDICATE)`, duck-typed `client=` injectable; low-confidence lands `status="pending_review"`. Accept the one qwen3 reload between ADJUDICATE and TAG per pass.
- **Precision harness:** a committed hand-labeled JSON fixture (~50 aerospace-flavored sentences with expected extractions) and a test asserting a precision floor on the regex path — wrong units are worse than missing ones.
- **API** (`routers/quantities.py`): `GET /api/quantities/kinds` (kinds, counts, min/max), `GET /api/quantities/search?kind&min&max` → paper_ids, `GET /api/papers/{id}/quantities`, `POST /api/quantities/{id}/review` (confirm/reject). *(Per-cluster rollup deferred — a GROUP BY that can land any time; the demo path is kinds → range filter → DetailPanel → review queue.)*
- **Frontend:** `quantityResults: Set<paper_id> | null` mirroring `searchResults` exactly — one intersection added in `lib/filtering.ts` (+ resets in `clearFilters`/`setGraph`), **zero changes** to PointCloud/ListView/counter; `panels/QuantityFilter.tsx` (kind picker + min/max) in the FilterBar; DetailPanel "Quantities" section between Placement and Nearest (the `<dl className="placement">` pattern, sentence tooltips); review queue view following the quarantine-table pattern.

**Verification:** on the labeled fixture, regex precision ≥ the floor; end-to-end on a real corpus: filter "altitude 400–800 km" dims the map to the right papers, each row's tooltip shows its source sentence, a wrong extraction can be rejected from the review queue and disappears from filters; `eval` numbers (rows extracted, precision on fixture) recorded in the README's measured-numbers style.

---

## Commit sequences

Every commit leaves all four gates green. New tables appear in tests automatically via `Base.metadata.create_all` (conftest), so migrations additionally get verified with `alembic upgrade head` against a **copy** of the real `data/scinet.db` before each phase merges.

### Phase 0 — infra (3 commits, branch `feat/phase0-infra`)

1. `ci: the suite has been green for weeks — make it prove it` — `.github/workflows/ci.yml` (backend + frontend jobs), badges, README note.
2. `feat(cli): scinet up — the three terminals become one` — `app/cli/up.py`, `scinet-up` entry, `tests/test_up.py` (dry-run, command construction, port-busy refusal; no real spawns).
3. `feat(demo): a scripted camera for recorded demos` — `scripts/record_demo.mjs` + `scripts/scenarios/smoke.mjs`; manual gate, not CI.

### Phase 1 — MCP (5 commits, branch `feat/mcp-server`)

1. `feat(mcp): a stdio server that proxies the running api` — pyproject (`mcp>=1.2,<2`, `scinet-mcp` script), `app/mcp/server.py` with `build_server(base_url, client_factory)` (tests inject `httpx.ASGITransport(create_app())` — no lifespan, so no WARMER thread), tools `search_library` + `get_paper`; `tests/test_mcp_server.py` via the SDK's in-memory client session.
2. `feat(mcp): read a paper as a markdown window, and its neighbours` — `read_paper(paper_id, offset, window)` slicing `/markdown` with total-length metadata; `similar_papers`.
3. `feat(mcp): regions and a library overview` — `list_regions`/`region_details`/`library_overview`; seeded-projection fixtures borrowed from `test_graph_api.py`.
4. `feat(mcp): say clearly when the api is down` — `httpx.ConnectError` → one message naming the URL and the start command; 404 details passed through; raising-transport tests.
5. `docs(mcp): wire scinet-mcp into a client` — docs/MCP.md (`claude mcp add scinet -- uv --directory backend run scinet-mcp`), README/USAGE/CLAUDE.md commands.

### Phase 1b — scrubber (2 commits, branch `feat/time-scrubber`)

1. `feat(ui): scrub the library through time` — `yearCutoff` store field + filtering intersection + FilterBar slider/play + tests.
2. `docs(demo): the corpus history, recorded` — scrubber scenario; GIF into README.

### Phase 2 — Model Lab (8 + 1 stretch, branch `feat/model-lab`)

1. `fix(models): probe the card doctor was guessing at, and crashing on` — `core/hardware.py`, doctor kwarg fix, lazy GpuSlot default, `tests/test_hardware.py`; `test_gpu_lifecycle` untouched and green.
2. `fix(models): one server of record for availability and generation` — `PrivateOllama.url` honors `ollama_url_override`; `start()` no-ops under override (today `tier2_vlm.available()` spawns a private server just to check tags); `free_all_models` unloads whatever `/api/ps` reports via new `resident_models()` instead of the hardcoded pair (`gpu.py:146`). Duck-typed fake-response tests.
3. `feat(models): measured facts live in the database, seeded from the registry` — `ModelProfile` model + re-export; migration `d4e5f6a7b8c9`; `services/models/profiles.py` idempotent seed (REGISTRY as `source="builtin"`, `REJECTED_MODELS` → `verdict="cpu-only"` — its first consumer ever).
4. `feat(models): a task router — pin beats profile beats default` — `core/model_router.py` (`TaskKind`, `resolve`); `model=` kwarg threaded through the three session-bearing seams; pin/profile/default precedence tests; default path byte-identical.
5. `feat(models): discover what both stores actually hold` — `services/models/discovery.py` (`/api/tags` + `/api/show` on the private URL and, if answering, 11434 — localhost, no egress concern); `routers/models.py` `GET /api/models`; registered in `create_app`.
6. `feat(models): measurement is a job, deduped per reference` — `JobKind.MEASURE` + `PRIORITY 90`, `enqueue_measure` + `handle_measure` (classify per `download_models.py:141-151`; tok/s; self-unload; timeout → cpu-only-suspect note), HANDLERS entry, `POST /api/models/measure`; `download_models.py --measure` rewired to persist. Tests: dedupe same/different refs; `run_stage`-pattern drive.
7. `feat(models): embed models report footprint, speed, and their real dimension` — HF measure path wiring `encoder.embedding_dimension()`; fake-SentenceTransformer tests.
8. `feat(ui): the model lab, a fourth view` — `ViewMode 'models'`, App branch, FilterBar toggle (gate the tag row/count to the map view — they currently render unconditionally), `panels/ModelLab.tsx` (profile table with verdict badges + measure buttons + per-task pin selects, hardware header **with a live residency gauge** — `GET /api/models` includes what `/api/ps` reports resident, polled at the JobsDrawer's 4 s cadence), `api/models.ts`; bun-test the response mapper.
9. *(stretch)* `feat(models): a self-supervised ocr benchmark` — render known-text page → VLM → char accuracy; schema-compliance scoring.

### Phase 2b — morph view (3 commits, branch `feat/morph-view`)

1. `feat(project): a second opinion — alternate-model projection runs` — script + `run_id` param + tests (alt run inactive, aligned, active map byte-identical).
2. `feat(ui): the morph slider between two embeddings` — dual-buffer fetch + lerp + picker; bun test the lerp math.
3. `docs(demo): watch two models disagree` — morph scenario GIF; mean-displacement number into README.

### Phase 3 — Librarian (9 + 1 stretch, branch `feat/librarian`)

1. `feat(worker): a pause lease the api can take and the worker honours` — `workers/lease.py` (nonce'd lease/ack), checks at `runner.py:80` and `:98`, keep-warm rule; plus manual-lease endpoints `POST /api/jobs/pause` (takes a `reason:"user"` lease, longer TTL) and `DELETE /api/jobs/pause` in `routers/jobs.py` — the librarian and the UI pause button share one mechanism. Tests: leased → `drain_stage` claims nothing (job queued, attempts 0), expired lease ignored, ack echoes nonce, resume after expiry (fixture pattern `test_worker_resilience.py:24-29`).
2. `feat(librarian): tools over the library, in process` — `services/librarian/tools.py` (fulltext reusing the FTS query shape `search.py:79-99`, WARMER-gated semantic, `VectorStore.nearest`, markdown windows, cluster lookup; every result carries its evidence paper_ids); seeded-DB tests.
3. `feat(librarian): a bounded tool loop that decides before it speaks` — `agent.py`, planning schema `action`-enum-first, ≤3 rounds, async duck-typed `client=`; scripted-fake walk tests.
4. `feat(librarian): the answer streams over sse, and the map listens` — `routers/librarian.py` POST `/ask` (events.py framing verbatim; `anyio.to_thread.run_sync` around sync tools; lease take/refresh/ack-wait/`finally`-release; `asyncio.Lock` single-flight → 409); `TestClient.stream` frame-collection tests with a fake agent.
5. `feat(librarian): every citation is checked against the evidence` — in-stream `[#id]` marker validation/strip; `done` frame carries cited + dropped.
6. `feat(ui): the librarian panel, and the first hand-rolled sse reader` — `api/librarian.ts` with the frame parser exported as a pure function (bun-testable without fetch) + reader loop + AbortController; store slice (messages panel-only); `panels/LibrarianPanel.tsx`; EngineWarming reuse; citation chips.
7. `feat(ui): the camera answers, and cited papers pulse` — `cameraRequest` nonce slice + CameraRig effect calling the existing `flyTo`; `GraphBuffers.highlight` + `aHighlight` + `uTime` pulse (the 5 known edits; highlight stays out of `gl_PointSize` so the pick shader is untouched); `lib/framing.ts` for frame-this-set distance; extended bun graph tests.
8. `feat(librarian): the stream says what the gpu is doing` — ack-wait/proceed-without-ack/warming status frames, timeouts finalized, USAGE docs; no-ack path tests.
9. `feat(ui): pause the pipeline from the map` — a pause/resume toggle in the JobsDrawer header driving the commit-1 endpoints, paused state via the existing pulse chip (amber, distinct from idle); `WATCHED` gains the pause events if published.
10. *(stretch)* `feat(ui): the trail of the evidence` — `graph/Trail.tsx` (drei dashed Line, dashOffset in useFrame, memoized points).

### Phase 4 — Quantities (7 + 1 stretch, branch `feat/quantities`)

1. `feat(quantities): the table, and pint` — `models/quantity.py` (keyed `(paper_id, chunk_ord)`, indexes `(quantity_kind, value_si)` + `paper_id`), migration `e5f6a7b8c9d0` on `d4e5f6a7b8c9`, pyproject `pint`; round-trip test.
2. `feat(quantities): precision-first extraction with a labelled floor` — `services/quantities/extract.py` (singleton `UnitRegistry`); `tests/fixtures/quantities_labeled.json` (~50 aerospace-flavored sentences); precision ≥ 0.9 gated, recall reported not gated.
3. `feat(quantities): an extract stage between embed and project` — `JobKind.EXTRACT` + `PRIORITY 35` + handler + HANDLERS entry **in the same commit** (an enqueued kind without a handler sits queued forever); delete-then-insert; enqueue from `handle_embed`; enqueues ADJUDICATE only when pending rows written; `run_stage` tests.
4. `feat(quantities): adjudication batches with the llm era` — `ADJUDICATE` + `PRIORITY 95`, batch-25 with per-batch commits, kind-enum-first schema, router-resolved model, `client=` injectable; scripted-fake confirm/reject/renormalize tests.
5. `feat(quantities): the api — kinds, ranges, and review` — `routers/quantities.py` + create_app wiring; TestClient tests.
6. `feat(ui): filter the map by a measured range` — `quantityResults: Set<number> | null` mirroring `searchResults`; third intersection in `useVisibleSet` (+ resets in `clearFilters` AND `setGraph`); FilterBar kind+min/max control; `api/quantities.ts`; `filtering.test.ts` extended. Zero PointCloud changes.
7. `feat(ui): quantities on the paper card, and a review queue` — DetailPanel section between Placement(:152) and Nearest(:154) with sentence tooltips; `panels/QuantityReview.tsx` in the quarantine-view style; new worker events added to `WATCHED` (`sse.ts:16-21`) — extract completion published in aggregate, not per paper (broker drops oldest past 256).
8. *(stretch)* `feat(ui): a scatter of what the field reports` — inline-SVG scatter (value_si vs. year, colored by the cluster palette) fed by `/api/quantities/search` results joined to graph nodes; no chart dependency; the micro-meta-analysis money shot, recorded as this phase's demo scenario.

### Track C — aerospace demo corpus (content, parallel from Phase 1)

Build the PROJECT_AUDIT §4 corpus (~420 open-licensed docs: arXiv subfields, scanned NASA NTRS reports, NASA EPUB/MOBI ebooks, 1-2 public-domain DjVu classics), files named `Domain_*.ext` so `scripts/eval_clustering.py` scores it as ground truth. Deliverables: `demo/manifest.jsonl` (source URL + SHA-256 per doc) + `scripts/fetch_demo_corpus.py` (a standalone, user-invoked downloader like `download_models.py` — not app egress; the app's enrichment gate is untouched) + the ingested library with its measured ARI recorded in the README's numbers table. Every phase's demo scenario (Phase 0 harness) records against this corpus, so each GIF doubles as domain signal for the CV.

## Cross-cutting risks & mitigations

- **TestClient runs the lifespan → `WARMER.start()` fires in API tests.** Tolerated today; new librarian tests must never depend on it — inject fakes, never reach `encoder.encode_query`. MCP tests dodge it entirely (ASGITransport runs no lifespan).
- **`get_settings` lru_cache:** anything called outside a request (handlers, agent, measure) takes `settings` as a parameter, house-style; `model_router.resolve(session=None)` skips pins rather than opening its own session.
- **New env fields** (at most `SCINET_LIBRARIAN_MODEL=""` — empty ⇒ router-resolved): add to `config.py` and `.env.example` in the same commit (`extra="ignore"` makes additions safe; drift there was an audit finding).
- **CORS: no changes.** Same-origin via Vite proxy for the browser; MCP is a separate localhost process (CORS inapplicable); GET/POST/DELETE already covers everything planned.
- **Migration chain stays linear by build order:** `d4e5f6a7b8c9` (model_profiles, Phase 2) → `e5f6a7b8c9d0` (quantities, Phase 4); MCP and Librarian touch no schema (the lease uses the existing `settings` table). Because tests use `create_all`, model/migration mismatch is invisible to pytest — hence the upgrade-against-a-DB-copy gate per phase merge.
- **Ollama `format` + `stream:true` composition trap:** locked out by design — schemas only on non-streamed planning calls; the streamed answer is marker-validated prose.
- **Keep-warm identity:** compare `reference` strings, never `ModelEntry.key` (key embeds the role; qwen3-as-TAG vs qwen3-as-LIBRARIAN would never match and always cold-swap).
- **Ollama split-brain** (override vs private URL) is fixed in Phase 2 commit 2 *before* the Librarian leans on residency logic.

## Explicitly deferred (named so they don't creep back silently)

Self-supervised OCR + schema-compliance benchmarks (Phase 2 stretch) · the evidence Trail (Phase 3 stretch) · per-cluster quantities rollup · full router migration of all ~20 model call sites · multi-turn librarian memory / parallel asks · results-tables meta-analysis (approved out of scope).

## Verification summary

- Per commit: `cd backend && uv run pytest` · `uv run ruff check . ../scripts && uv run ruff format --check . ../scripts` · `cd frontend && bun run typecheck && bun test` — and, from Phase 0 onward, the same four gates in GitHub Actions on every push.
- Per phase: the phase's end-to-end verification above; `bun scripts/verify_render.mjs` after any graph/shader change (Phases 1b, 2b, 3); `alembic upgrade head` against a copy of the real DB before merging Phases 2 and 4; a manual MCP session (Phase 1).
- Per phase deliverable: a recorded demo GIF via the Phase 0 harness (`scripts/record_demo.mjs` + the phase's scenario file), captured against the Track C corpus once it exists.
- Docs per phase: README + USAGE + `.env.example` + CLAUDE.md commands section updated in the phase's final commit.
