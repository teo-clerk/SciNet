# Using SciNet

A guide to running SciNet from a fresh checkout, importing a library, and
reading the map.

---

## 1. First-time setup

You need Python 3.12 (pinned — `umap-learn` is not tested above it), Node 20+
or bun, and the `ollama` binary. SciNet uses its own copies of every model, so
nothing you have installed globally matters.

```bash
cp .env.example .env          # review paths and the privacy switch

cd backend
uv sync --group dev           # core stack
uv run alembic upgrade head   # creates data/scinet.db

cd ../frontend
bun install
```

> **On Windows**, run the same commands in PowerShell. Two differences:
> `cp` is `copy`, and paths use backslashes. Everything else — `uv`, `bun`,
> `ollama` — works identically.
>
> ```powershell
> copy .env.example .env
> cd backend
> uv sync --group dev
> uv run alembic upgrade head
> cd ..\frontend
> bun install
> ```
>
> See [§7](#7-windows-notes) for the couple of places behaviour genuinely
> differs.

Then fetch the models — about 11 GB, into `data/models/`:

```bash
cd backend
uv run python ../scripts/download_models.py
```

Check the machine can actually run them before starting a long import. This
catches the failure that otherwise looks like a hang:

```bash
uv run python ../scripts/doctor.py
```

A model reported as `CPU-ONLY` will work but roughly twenty times slower.

---

## 2. Running it

SciNet is three processes. Each wants its own terminal.

```bash
# 1 — the API. Binds to 127.0.0.1 and nothing else.
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2 — the worker. All parsing, embedding and tagging happens here.
cd backend && uv run python -m app.workers.runner

# 3 — the interface.
cd frontend && bun run dev
```

Open <http://localhost:5173>.

The API answers in under a second. The embedding model loads in the background
and takes about 25 seconds; until it finishes, semantic search says so and
retries itself. Everything else — the map, full-text search, opening PDFs —
works immediately.

**You can close the worker whenever you like.** The map is on disk, so the API
serves it without the worker running. Papers only move through the pipeline
while the worker is up, and anything left unfinished resumes when it restarts.

---

## 3. Getting papers in

Three ways, all equivalent:

**Drag them into the window.** The Upload button in the toolbar accepts a
multi-file selection and copies them into the library. Progress appears in the
drawer at the bottom left.

**Drop files into `data/library/`.** The watcher notices them within a couple of
seconds. It waits for the file to stop changing before reading it, so copying a
large PDF in is safe.

**Import an existing collection:**

```bash
cd backend && uv run python ../scripts/backfill.py ~/Papers
```

Backfill is resumable. Interrupt it and run it again; it picks up where it
stopped, because registration is keyed on file content and the job queue is
durable.

### What happens to a paper

```
parse ─→ metadata ─→ embed ─→ project ─→ tag
```

Each stage runs to completion across the whole queue before the next begins.
That looks odd if you are watching one paper, but it is deliberate: the graphics
card holds one model at a time, and switching per paper would spend more time
loading models than working.

A paper becomes visible on the map after `project`. Tags and summaries arrive
later — the map is useful before they land.

### How long it takes

Measured on 300 real arXiv papers, 8,000 pages, on an RTX 4060 laptop:

| stage | cost |
|---|---|
| parse | ~390 ms/page, CPU |
| embed | ~1 s/paper |
| project | ~1 min for the whole corpus |
| tag | ~6 s/paper |

About 50 minutes end to end, dominated by tagging. Tagging runs last precisely
so you can start reading the map while it finishes.

---

## 4. Reading the map

Position means something. Papers are placed by what they are about, so
distance is similarity and the clusters are real groupings rather than
decoration.

### Moving around

| | |
|---|---|
| **Left-click drag** | rotate |
| **Right-click drag** | pan |
| **Scroll** | zoom |
| **Click a node** | select it, fly to it, open the sidebar |
| **Hover** | highlight and label a node |
| **Escape** | close the sidebar |

Hovering is suppressed while you are dragging — the pointer is moving the scene
then, not pointing at anything.

Paper titles appear only when you are close enough to read them. Pulled back,
the cluster names carry the overview.

### What you are looking at

- **Colour** is the cluster, by default. Switch to **Year** to see how the
  library grew, or **Provisional** to find papers placed without a full refit.
- **Size** is the paper's length, log-scaled.
- **Faint lines** connect each paper to its two nearest neighbours. They trace
  the structure the projection flattened; dense regions glow where many overlap.
- **Bright lines** appear when you select a paper, connecting it to its five
  nearest neighbours — computed on meaning, not on screen distance.

### Searching

Three modes, because they answer different questions:

| mode | finds | runs |
|---|---|---|
| **Title** | words in titles, typo-tolerant | in the browser, instantly |
| **Full text** | exact phrases anywhere in the paper | server, FTS5 index |
| **Meaning** | papers about the same thing in other words | server, embeddings |

Searching dims the rest of the map rather than hiding it, so a result keeps the
context that tells you where it sits.

Tag filters combine with OR: picking two topics widens the view.

---

## 5. Privacy

Everything runs locally. Parsing, embedding, tagging and search all happen on
your machine, and the API listens only on `127.0.0.1`.

One optional exception: enrichment, which looks up DOIs against Crossref to fix
titles and venues. It is **off** by default. Turn it on with
`SCINET_ENRICHMENT_ENABLED=true`, and every request it makes is recorded:

```bash
sqlite3 data/scinet.db "SELECT ts, service, url FROM egress_log ORDER BY ts DESC LIMIT 20"
```

Enabling it tells those services which papers you read. That is the trade.

---

## 6. When something looks wrong

```bash
# Where is everything?
sqlite3 data/scinet.db "SELECT status, count(*) FROM papers GROUP BY status"
sqlite3 data/scinet.db "SELECT kind, state, count(*) FROM jobs GROUP BY 1,2"

# Jobs that gave up, and why
curl -s localhost:8000/api/jobs/dead | head

# Retry them
curl -X POST localhost:8000/api/jobs/dead/retry
```

**The map is empty.** No projection has been computed yet — the worker needs to
reach the `project` stage. Below 200 papers it uses PCA, which is deterministic
and instant; above that it fits UMAP.

**A paper is missing.** It may be a duplicate. SciNet skips files whose content
is byte-identical to something already imported, and flags papers that look like
the same work under a different file.

**Ingestion seems stuck.** Run `scripts/doctor.py`. The usual cause is a model
too large for the card being served from system RAM, which is silent and about
twenty times slower.

**Everything is slow while importing.** Expected — the worker is using the whole
machine. It is safe to stop it and restart later.


---

## 7. Windows notes

SciNet runs on Windows without changes. Use PowerShell and the same commands;
these are the only places behaviour actually differs.

### Running it

```powershell
# 1 — API
cd backend; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 2 — worker
cd backend; uv run python -m app.workers.runner

# 3 — interface
cd frontend; bun run dev
```

PowerShell separates commands with `;` rather than `&&`. Environment variables
for a single run are set differently too:

```powershell
$env:SCINET_ENRICHMENT_ENABLED = "true"; uv run python -m app.workers.runner
```

### Opening a PDF

Works as-is. The "Open PDF" button picks the right command per platform —
`start` on Windows, `open` on macOS, `xdg-open` on Linux — and hands the file
to whatever your system uses for PDFs. The path guard around it is
platform-independent and applies everywhere.

### Paths

Set them with forward slashes in `.env` — Python accepts them on Windows and it
avoids escaping backslashes:

```
SCINET_LIBRARY_DIR=C:/Users/you/Documents/Papers
```

An absolute path outside the project is fine; the library does not have to live
under `data/`.

### GPU

CUDA works the same way. `scripts/doctor.py` reports what it finds, and the
same warning applies: a model too large for the card is served from system RAM
silently, about twenty times slower.

Without an NVIDIA GPU everything still runs — tier-0 parsing and the embedding
model are CPU paths already. Tagging with `qwen3:8b` on CPU is slow enough that
you may want `SCINET_LLM_MODEL=qwen3:4b`, or to leave tagging to finish
overnight. The map itself does not need it.

### Line endings

The repository has no `.gitattributes`, so Git may convert line endings on
checkout. Nothing in SciNet parses its own source at runtime, so this is
harmless — but if you edit `.env` in Notepad, save it as UTF-8 without a BOM.
