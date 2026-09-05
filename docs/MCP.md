# SciNet as MCP context

`scinet-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio. Point any MCP client at it — Claude Code, Claude Desktop,
an IDE — and that assistant can search your library, read your papers, and
walk the map's regions while you work: cite your own papers in a draft, pull
a method's details into a code comment, ask "have I read anything about
this?" from wherever you are.

It **proxies the running API** rather than opening the database itself. The
API already owns the hard parts — the embedding model warms once with an
honest "warming" state, and queries run on CPU so they can never fight the
worker for VRAM — so the one requirement is that SciNet is up:

```bash
cd backend && uv run scinet-up        # or just the API: uvicorn app.main:app
```

Every tool tells the agent exactly that, with the command, when the API is
not answering.

## Wiring

The **⚡ AI tools** button in the app's top bar shows every snippet below with
this checkout's path already filled in, and a Copy button beside each. They
come from `GET /api/mcp`, which also lists the tools by asking the server
itself — so the app, this page and the code cannot disagree about them (a
test holds the table below to that list).

**Claude Code:**

```bash
claude mcp add scinet -- uv --directory /path/to/SciNet/backend run scinet-mcp
```

Add `-s user` to make it available in every project rather than the current
one.

**Claude Desktop** — `claude_desktop_config.json`, at
`~/Library/Application Support/Claude/` on macOS and `%APPDATA%\Claude\` on
Windows; merge under an existing `mcpServers` key and restart the app:

```json
{
  "mcpServers": {
    "scinet": {
      "command": "uv",
      "args": ["--directory", "/path/to/SciNet/backend", "run", "scinet-mcp"]
    }
  }
}
```

**Cursor** — the same JSON, in `~/.cursor/mcp.json` for every project or
`.cursor/mcp.json` inside one project.

**Zed** — `settings.json` (Zed › Settings › Open Settings), or
Settings › AI › MCP Servers › Add Local Server:

```json
{
  "context_servers": {
    "scinet": {
      "command": "uv",
      "args": ["--directory", "/path/to/SciNet/backend", "run", "scinet-mcp"],
      "env": {}
    }
  }
}
```

If the API listens somewhere other than port 8000, the spawned server needs
`SCINET_API_URL` (e.g. `http://127.0.0.1:8123`) in its environment — the
dialog adds it to every snippet for you when that is the case, and leaves it
out otherwise, so the default snippet is exactly what you see here.

## Tools

| Tool | What it answers |
|---|---|
| `library_overview()` | Counts, regions, tags — the survey to start with |
| `search_library(query, mode, limit)` | `semantic` finds meaning, `fulltext` finds exact words with snippets, `title` is a substring match |
| `get_paper(paper_id)` | Metadata, abstract, map placement — trimmed for a model's context |
| `read_paper(paper_id, offset, window)` | The parsed markdown in capped windows; page with `next_offset` |
| `similar_papers(paper_id, k)` | Nearest neighbours in embedding space (never the 3D coordinates) |
| `list_regions()` | The map's named clusters with sizes and top terms |
| `region_details(cluster_id)` | One region's overview and most representative members |
| `find_semantic_path(from_concept, to_concept)` | The chain of works from one idea to another — phrases or paper ids; each stop carries the work's central question and its region, and a jump the library cannot bridge is reported, never hidden |
| `get_curriculum(topic_or_cluster)` | Where to start reading a region or a topic, with the reasons, and the order to read the rest |
| `query_quantities(quantity_kind, unit, min_val, max_val, query, limit)` | Measured values across the library with the sentence each came from — by kind, unit, SI range or phrase; trusted rows only |

## Claude Code as a research librarian

The repository ships a skill, `.claude/skills/scinet-researcher/SKILL.md`,
that Claude Code loads automatically in a session opened here — and that
you can copy into any other project's `.claude/skills/` (or `~/.claude/skills/`
for every project). It teaches the assistant to work the tools in three
steps rather than guessing:

1. **Orient** — `library_overview`, then `list_regions`: name regions as the
   library names them, never invent one.
2. **Structure** — `find_semantic_path` for how two ideas connect (read
   `regions_crossed` and each stop's central question; say so when the chain
   is not continuous); `get_curriculum` for where to start and in what order,
   with the reasons verbatim; `query_quantities` for numbers, quoting the
   sentence each came from.
3. **Evidence** — `read_paper` in windows for verbatim passages, every claim
   carrying `[#paper_id]`, never a paper no tool returned; a `warming` answer
   means wait and retry once.

## Properties worth knowing

- **Read-only.** Nothing an agent does through these tools can change the
  library; ingestion stays with the folder watcher, the upload button, and
  backfill.
- **Local-only.** The server talks to `127.0.0.1` and nothing else; the
  privacy story is exactly the app's.
- **Warming is an answer, not an error.** While the embedding model loads
  (~25 s after API start), semantic search returns a message with the ETA so
  the agent retries instead of concluding "no results".
- **A library with no map yet** (still processing) answers region tools with
  a note rather than an error — that state is normal.
