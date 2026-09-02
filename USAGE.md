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

One command starts everything:

```bash
cd backend && uv run scinet-up          # API + worker + UI, logs in one terminal
```

Ctrl+C stops all three. `--no-worker` serves the existing map without
processing anything; `--dry-run` prints what would start. `uv run scinet-stop`
still works from anywhere and stops whatever is running, however it was
started.

Prefer separate terminals — for keeping the worker's log apart, or for
restarting one piece without the others? SciNet is three processes:

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

### Stopping everything

Closing a terminal does not always take its process with it, and a stale
process is worse than an obvious one: a second worker fights the first for the
database write lock, and an API left over from an earlier session serves routes
that no longer exist in the code you are editing.

```bash
cd backend && uv run scinet-stop           # stop the API, the worker, and Vite
cd backend && uv run scinet-stop --dry-run # show what is running, change nothing

./scripts/stop.sh                          # the same, from anywhere in the repo
```

Both run the same code, which works on Linux, macOS and Windows. It finds
processes by the port they listen on and by the binary they are *running* — not
by matching text in a command line — so it will not match, or kill, the shell
you type it into.

---

## 3. Getting papers in

Three ways, all equivalent:

**Drag them into the window.** The Upload button in the toolbar accepts a
multi-file selection and copies them into the library. Progress appears in the
drawer at the bottom left.

**Drop files into `data/library/`.** The watcher notices them within a couple of
seconds. It waits for the file to stop changing before reading it, so copying a
large PDF in is safe. **Subfolders work** — organise the library by year, topic
or reading list however you like; the scan is recursive.

**Import an existing collection:**

```bash
cd backend && uv run python ../scripts/backfill.py ~/Papers
```

Backfill is resumable. Interrupt it and run it again; it picks up where it
stopped, because registration is keyed on file content and the job queue is
durable.

### What counts as a paper

| format | how it is read |
|---|---|
| `.pdf` | tiered: fast text layer, escalating to OCR only when needed |
| `.txt`, `.md` | read directly as UTF-8 |
| `.docx` | `python-docx`; Word heading styles become Markdown headings |
| `.epub` | unzipped directly, so chapter headings and the real title, author and publication date all survive |
| `.mobi`, `.azw3` | read through MuPDF, which handles the PalmDOC/KF8 compression |
| `.djvu` | the OCR text layer the scanner saved, decoded in-process |
| no extension | PDFs, DjVu and MOBI are recognised by content, so bare arXiv and archive downloads work |

Everything lands in the same pipeline. A book is embedded, positioned,
clustered and tagged exactly like a paper, and sits next to the papers on its
subject rather than in a "books" corner of the map.

Two things are worth knowing:

- **A DjVu with no text layer cannot be read.** DjVu holds page images plus
  whatever text the scanning software recognised. If nobody ever ran OCR on it,
  there is no text to recover and the file is reported as failed rather than
  ingested blank. Run it through OCR yourself and re-add it.
- **DRM-protected books cannot be read by anything**, this included. An
  `.azw3` bought from a store will be rejected with a clear message.

### Only the first 80 pages are read

A library is bimodal: papers run to a couple of dozen pages, books to many
hundreds, and there is almost nothing in between. Reading a 700-page book to
the end costs a great deal and tells the map nothing it did not already know
from the title, preface and opening chapters — and if the book is a scan, the
vision model charges 8-15 seconds a *page* for the privilege.

So parsing stops at 80 pages. Every paper is read whole; a book is read as far
as its introduction and early chapters, which is what decides where it sits.
The Markdown ends with a line saying how much was skipped, and the page count
shown in the sidebar is still the real one — a truncated 731-page book is
still a 731-page book.

Set `SCINET_MAX_PARSE_PAGES` in `.env` to change it — `0` reads everything:

```bash
echo "SCINET_MAX_PARSE_PAGES=0" >> .env
```

Documents already parsed keep whatever they have; the limit applies to the next
parse.

### Books have no abstract

A paper says what it is about in its abstract. A book opens with a title page,
a copyright notice, a dedication and a table of contents — thousands of
characters containing nothing about the subject.

So the abstract is looked for in five places, strongest evidence first:

1. a heading that says **Abstract**;
2. a **preface**, foreword or prologue — a book's abstract under another name;
3. an unlabelled opening paragraph shaped like an abstract (preprints do this);
4. the opening of an **introduction**;
5. failing all of that, a **digest** assembled from the document's own most
   topical paragraphs, sampled across the whole document rather than taken from
   the front.

Only the last invents anything, and the sidebar marks it as assembled rather
than written.

If you improve on this later, existing rows keep whatever the rules said the
day they were parsed. `scripts/fix_abstracts.py` re-derives the ones that are
demonstrably wrong — a keywords table, a page header, a figure caption — and
leaves good ones alone. It is a dry run unless you pass `--apply`, and because
an abstract feeds the document vector, the map only reflects the change after a
re-embed. Rung 3 is switched off for anything longer than about 65 pages:
"the first substantial paragraph is the abstract" is a paper's rule, and
applied to a book it returns the opening of chapter one.

### Nothing is parsed twice

**Parsing is fully persistent. Restarting the app never re-parses anything.**

Every stage writes its result to disk and is keyed on file *content*, not
filename or modification time:

- Extracted Markdown lives in `data/markdown/`.
- Vectors live in a memmap in `data/vectors/`; the fitted UMAP reducer is
  serialised beside them.
- Coordinates, clusters and metadata live in SQLite.

So starting the app opens the existing map immediately — no refit, no
re-embedding, no re-reading of PDFs. Only files that are genuinely new are
processed, and they are added *incrementally*: a new paper is projected through
the existing reducer with `transform()`, which takes seconds and leaves every
existing node exactly where it was.

Re-running `backfill.py` over the same folder is therefore cheap and safe — it
registers nothing it already has. Renaming or moving a file is recognised as the
same paper and simply updates its path.

Work is only redone when you ask for it: changing the embedding model
invalidates vectors and projections (but never the extracted Markdown), and a
full re-projection has to be requested explicitly.

### Rebuilding the map by hand

The map refits itself when the stored fit stops describing the corpus. To force
it — after changing the embedding model, or a large import:

```bash
cd backend && uv run python ../scripts/force_project.py            # what it would do
cd backend && uv run python ../scripts/force_project.py --apply    # enqueue it
```

`--apply` only enqueues, which is safe with the worker running: it picks the job
up like any other. Add `--run` to do the work in this process instead, which
refuses to start if a worker is already alive.

A refit is not destructive. It computes a whole new layout into an inactive
run, aligns it onto the current one so the map settles rather than scrambling,
and swaps in a single statement — so you see the old map or the new one, never
a mixture, and a refit that dies leaves the previous map intact.

### Jobs that died of something you have since fixed

`dead` means the queue gave up after three attempts. That is right for a
document nothing can read, and wrong for a stage that failed because a library
was missing — every attempt hit the same import error, and nothing retries them
afterwards because being out of retries is exactly what `dead` records.

```bash
cd backend && uv run python ../scripts/revive_jobs.py           # show
cd backend && uv run python ../scripts/revive_jobs.py --apply   # requeue
```

It revives only jobs whose recorded error names a known environmental cause,
and checks the module actually imports first — so an unreadable book stays
dead, and you are not handed 449 jobs that are about to fail the same way
again. Stop the worker first; it is the sole writer.

### When a file cannot be read

Some files are not readable by anything: a DRM-locked Kindle book, a PDF whose
container is corrupt, a DjVu nobody ever ran through OCR. These look like
ordinary documents from the outside — nothing says otherwise until something
tries to read them.

When that happens the file is **moved out of the library** into
`data/quarantine/`, and a **Quarantine** tab appears next to *3D Map* and
*List* with a count. It shows each file's name and exactly why it was rejected
— "DRM-protected (Amazon encrypted container); no tool can read it" rather than
a stack trace — and a **Restore** button that puts it back and queues it again.

Two kinds are distinguished, because they call for different actions:

| badge | meaning | what to do |
|---|---|---|
| `unreadable` | the format itself cannot be read | find a DRM-free or non-scanned copy |
| `gave up` | a stage ran out of retries | start the worker or the model server, then restore |

Nothing is deleted, and the reason is written to a `manifest.json` beside the
files as well. A `gave up` file is very often fine — if the model server was
down when it was parsed, restoring it is all that is needed.

### Broken files and duplicates

Libraries accumulate things that are not papers: HTML paywall pages saved with a
`.pdf` extension, truncated downloads, zero-byte placeholders, and the same
paper downloaded three times. These used to be skipped silently on every scan.
Now they are taken out:

```bash
cd backend && uv run python ../scripts/clean_library.py --dry-run     # report only
cd backend && uv run python ../scripts/clean_library.py               # delete
cd backend && uv run python ../scripts/clean_library.py --quarantine  # move aside
```

Backfill runs this automatically before scanning; pass `--no-clean` to skip it.

**Matching files are deleted.** Run `--dry-run` first on a library this has
never seen — these are heuristics against your own collection, and a false
positive on a real paper cannot be undone. `--quarantine` moves files to
`data/quarantine/<timestamp>/` with a `manifest.json` recording why each one
went, which is the reviewable middle ground.

Only files that *claim* to be documents are ever touched. A cover image or a
`.bib` file sitting in the library is left exactly where it is. Duplicates must
be byte-identical — two versions of the same paper are two papers.

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

### The Models view

The **Models** tab is the machine's own report: which GPU was detected and
its real budget, every model in the catalog with its *measured* footprint
and verdict (a model never measured shows **unproven**, not a guess), models
found installed in your system Ollama that SciNet could use, and a routing
table that says which model answers which task. **Measure** warms a model on
the worker and records what actually happened — including the two shipped
rejections, kept visible so nobody re-downloads a 13 GiB mistake. Pinning a
task to a model is the only way routing changes; recommendations never act
on their own.

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

### Everything stays in the folder

SciNet is meant to be portable: zip the project, unzip it on another machine,
and it runs. That only holds if the models are inside it, and model libraries
do not do that by default — every one of them writes into your home directory
unless told otherwise, and when one slips through nothing fails. The model just
downloads again, into the wrong place, and the copy you moved turns out to be
hollow.

So it is checked rather than assumed:

```bash
cd backend && uv run python ../scripts/check_portability.py
```

It prints where every cache variable points, then looks in the usual global
locations (`~/.cache/huggingface`, `~/.ollama`, `~/.cache/torch` and the Windows
and macOS equivalents) for weights **this project is configured to use** — which
is what separates SciNet leaking into your home directory from your home
directory simply having models in it. It exits non-zero if it finds any, names
the exact directory, and tells you whether the project already has its own copy
before suggesting you delete anything.

One thing it reports and does not fix: Surya writes server lock and log files to
`~/.cache/datalab/surya`, a path hardcoded upstream with no setting behind it.
No weights go there, and it is only touched when tier 1 runs, which is off by
default.

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

**`THREE.Clock: This module has been deprecated`** in the browser console. This
comes from inside `@react-three/fiber`, not from SciNet — three.js 0.185
deprecated the class and the version of R3F we depend on still uses it. It is
harmless and disappears when R3F updates. Nothing in `src/` references it.


---

## 7. Sending this to someone else

The whole point of keeping the models inside `data/` is that a copy of this
folder runs on a machine that has never seen the project and never goes online.
That works — but the archive is large, so decide first which of the two you are
sending.

First, clear out what regenerates:

```bash
cd backend && uv run python ../scripts/prepare_export.py            # report
cd backend && uv run python ../scripts/prepare_export.py --apply    # clean
```

It prints the size of each part and never deletes anything the recipient
cannot rebuild — the library, the database, the extracted Markdown, the vectors
and the models all stay.

### What to leave out

| leave out | why | they rebuild it with |
|---|---|---|
| `backend/.venv` | a Linux virtualenv does not work on Windows | `uv sync` |
| `frontend/node_modules` | same, and it is in the lockfile | `bun install` |
| `frontend/dist` | build output | `bun run build` |
| `.git` | history is not needed to run it | — |

Those four are the difference between an archive that works and one that fails
confusingly on the other machine. The lockfiles (`uv.lock`, `bun.lock`) *are*
included, so both rebuild to the same versions.

### The models are the archive

On this library the split is roughly:

- `data/models` — **~20 GB**, the embedder and the local LLMs
- `data/library` — ~4 GB, the documents
- everything else — under 200 MB

**With the models** the recipient unzips and runs, offline, with nothing to
download. It is also past what email and most upload services accept, so it
means a hard drive or a self-hosted transfer.

**Without them** the archive is around 4 GB, and the recipient runs this once,
online:

```bash
cd backend && uv run python ../scripts/download_models.py
```

Everything else — their papers, the map, the clusters, the tags — is already in
the archive either way. Only the weights are missing, and only until that
command finishes.

### On the other machine

```bash
cd backend && uv sync --group dev
cd frontend && bun install
cd backend && uv run python ../scripts/check_portability.py   # confirms it is self-contained
```

---

## 8. Windows notes

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

To stop everything, call the Python script directly — `stop.sh` is a bash
wrapper, but the logic it wraps is cross-platform and uses `netstat` on Windows:

```powershell
cd backend; uv run python ../scripts/stop.py
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
