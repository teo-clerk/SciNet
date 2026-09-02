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

**Claude Code:**

```bash
claude mcp add scinet -- uv --directory /path/to/SciNet/backend run scinet-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):

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

If the API listens somewhere other than the configured port, set
`SCINET_API_URL` (e.g. `http://127.0.0.1:8000`) in the server's environment.

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

## Properties worth knowing

- **Read-only.** Nothing an agent does through these tools can change the
  library; ingestion stays with the watcher, the upload button, and backfill.
- **Local-only.** The server talks to `127.0.0.1` and nothing else; the
  privacy story is exactly the app's.
- **Warming is an answer, not an error.** While the embedding model loads
  (~25 s after API start), semantic search returns a message with the ETA so
  the agent retries instead of concluding "no results".
- **A library with no map yet** (still processing) answers region tools with
  a note rather than an error — that state is normal.
