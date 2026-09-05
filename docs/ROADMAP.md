# Roadmap — what would make SciNet a daily tool

The engine is done: parsing that survives hostile input, a map that keeps its
shape, structured extraction, a plain-English reading of every work, and an
MCP server that hands all of it to whatever assistant you already use. What
follows is ordered by the *loop* each item closes — the reason to open the
app tomorrow — and each is grounded in code that already exists.

| # | feature | the daily loop it closes | already in the tree | effort |
|---|---|---|---|---|
| 1 | **Watch roots** — the worker already watches `data/library/`; next, `SCINET_WATCH_DIRS` with several roots | save a PDF where you already save PDFs and it is on the map in a minute; point one root at `~/Zotero/storage` and the files sync themselves | `services/ingest/watcher.py`, the startup rescan | S |
| 2 | **Zotero import + BibTeX** — read `zotero.sqlite` and its storage folder; collections become tag proposals; Zotero's metadata outranks extraction through the provenance-ranked field sources; BibTeX export per work, selection or region | a decade of PDFs already in Zotero becomes a map without leaving Zotero | registration idempotent on content hash, `MetaSource` provenance, the `pending_review` tag queue | M |
| 3 | **Export to Obsidian / Markdown notes** — one note per work (front matter; the plain-English reading; measured values; `[[wikilinks]]` to the five nearest works and the region note), one note per region (members, bridges), a `_Map.md` index. A script first, then a button on the card and the region inspector | the map's reading becomes the vault's backlinks; every session ends with notes that link | `paper_insights`, `quantities`, `/api/graph/similar`, cluster bridges — all already in SQLite | S–M |
| 4 | **Inbox and "since you last looked"** — arrivals pulse until seen; a per-browser last-seen mark; a weekly digest written locally as Markdown into the notes folder: *12 new works; two landed in a region you had not read from; a bridge appeared between X and Y* | a reason to open it on Monday morning | `created_at`, bridges, the preference store | M |
| 5 | **Capture from anywhere** — `scinet add <path>` from the terminal; a browser "Save to SciNet" (an extension with host permission for `127.0.0.1:8000`, and the API's CORS allowlist extended to it — a bookmarklet cannot do this, CORS blocks arbitrary origins); and the one MCP *write* tool, `add_to_library(path)`, local file only, so the assistant can file the PDF it just fetched | zero-friction intake from the browser, the terminal and the assistant | `POST /api/papers/upload` (magic-byte checked), `egress_log` | S each |

And one for spreading: **share your map** as a single static HTML file — the
static-snapshot machinery a live demo would need, generalised. Every shared
map advertises the tool at no server cost.

Not on this list, and asked for often: **the seminal paper you do not own.**
That needs a citation graph — references parsed from every work, matched
against the library, and the ancestors the library keeps citing but does
not contain drawn as ghosts on the map. It is the largest item here and the
one that turns "what do I have" into "what am I missing". It waits on
reference parsing being reliable across the formats the library accepts.

Effort: S ≤ a week · M one to three weeks. Everything stays local; anything
that would open a socket goes through the enrichment gate and lands in
`egress_log`, which is currently a table with zero rows.
