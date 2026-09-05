"""The MCP server: tools an agent can point at the library.

Tool outputs are deliberately small and shaped for a language model, not a UI
— ids, titles, scores, short snippets. The full markdown of a paper is never
returned whole; ``read_paper`` windows it, because an MCP client's context is
somebody else's budget being spent.

`build_server` takes the base URL and an httpx client factory so tests can
run the entire stack in-process against ``create_app()``; `main` is the
console entry point (`uv run scinet-mcp`), stdio transport, spawned by the
MCP client itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.mcp.client import ApiClient, default_base_url

SEARCH_MODES = ("semantic", "fulltext", "title")
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 40
#: read_paper pages in windows: a book is hundreds of thousands of characters,
#: and an agent that asks for all of them at once has usually made a mistake.
DEFAULT_READ_WINDOW = 4_000
MAX_READ_WINDOW = 20_000
MAX_SIMILAR = 25
MAX_REGION_MEMBERS = 15
MAX_OVERVIEW_TAGS = 40

INSTRUCTIONS = (
    "SciNet is the user's local, private library of papers, books and essays "
    "from any discipline, already parsed, embedded, and clustered into a "
    "semantic map. "
    "Use search_library to find papers (semantic for meaning, fulltext for "
    "exact words, title for names you already know), then get_paper for "
    "metadata and the abstract. Paper ids are stable integers."
)


#: PaperDetail fields worth an agent's context. Everything else — work keys,
#: parse tiers, byte counts — is bookkeeping the reader can get from the UI.
_PAPER_FIELDS = (
    "id",
    "title",
    "authors",
    "year",
    "venue",
    "status",
    "abstract",
    "abstract_source",
    "summary",
    "tags",
    "doi",
    "arxiv_id",
    "page_count",
    "cluster_name",
    "cluster_confidence",
    "provisional",
)


def _trim_paper(detail: dict[str, Any]) -> dict[str, Any]:
    return {k: detail.get(k) for k in _PAPER_FIELDS if detail.get(k) is not None}


def _warming_answer(detail: dict[str, Any]) -> dict[str, Any]:
    """The 503-warming shape, translated for an agent instead of a spinner."""
    remaining = detail.get("estimated_remaining")
    return {
        "status": detail.get("state", "warming"),
        "message": (
            detail.get("message", "the search engine is still starting")
            + (f"; try again in ~{remaining:.0f}s" if remaining else "")
        ),
    }


def build_server(
    base_url: str | None = None,
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastMCP:
    api = ApiClient(base_url or default_base_url(), client_factory)
    server = FastMCP("scinet", instructions=INSTRUCTIONS)

    @server.tool()
    async def search_library(
        query: str, mode: str = "semantic", limit: int = DEFAULT_SEARCH_LIMIT
    ) -> dict[str, Any]:
        """Search the user's library.

        mode: "semantic" finds papers *about* the query even in other words;
        "fulltext" finds exact words and returns the matching snippet;
        "title" is a plain substring match on titles for names you already
        know. Returns {hits: [{paper_id, title, score?, snippet?, year?}]}.
        """
        if mode not in SEARCH_MODES:
            raise ValueError(f"mode must be one of {', '.join(SEARCH_MODES)}")
        limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))

        if mode == "title":
            status, body = await api.get_json(
                "/api/papers", params={"q": query, "limit": limit}
            )
            if status != 200:
                raise ValueError(f"the papers endpoint answered {status}: {body}")
            hits = [
                {"paper_id": p["id"], "title": p["title"], "year": p.get("year")}
                for p in body.get("items", [])
            ]
            return {"mode": mode, "query": query, "hits": hits}

        status, body = await api.get_json(
            f"/api/search/{mode}", params={"q": query, "limit": limit}
        )
        if status == 503 and isinstance(body, dict):
            return _warming_answer(body.get("detail", {}))
        if status != 200:
            raise ValueError(f"search answered {status}: {body}")
        hits = [
            {
                key: hit.get(key)
                for key in ("paper_id", "title", "score", "snippet", "section")
                if hit.get(key) is not None
            }
            for hit in body.get("hits", [])
        ]
        return {"mode": mode, "query": query, "hits": hits}

    @server.tool()
    async def get_paper(paper_id: int) -> dict[str, Any]:
        """Metadata, abstract, and map placement for one paper by id."""
        status, body = await api.get_json(f"/api/papers/{paper_id}")
        if status == 404:
            raise ValueError(f"no paper {paper_id} in the library")
        if status != 200:
            raise ValueError(f"the papers endpoint answered {status}: {body}")
        return _trim_paper(body)

    @server.tool()
    async def read_paper(
        paper_id: int, offset: int = 0, window: int = DEFAULT_READ_WINDOW
    ) -> dict[str, Any]:
        """Read a window of a paper's parsed full text (markdown).

        total_chars reports the document's size up front; page forward by
        calling again with next_offset until it comes back null. Windows are
        capped — a book is read in passes, not swallowed.
        """
        status, body = await api.get_json(f"/api/papers/{paper_id}/markdown")
        if status in (404, 410):
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ValueError(f"paper {paper_id}: {detail}")
        if status != 200:
            raise ValueError(f"the markdown endpoint answered {status}: {body}")
        text = body if isinstance(body, str) else str(body)
        offset = max(0, int(offset))
        window = max(1, min(int(window), MAX_READ_WINDOW))
        piece = text[offset : offset + window]
        end = offset + len(piece)
        return {
            "paper_id": paper_id,
            "total_chars": len(text),
            "offset": offset,
            "text": piece,
            "next_offset": end if end < len(text) else None,
        }

    @server.tool()
    async def similar_papers(paper_id: int, k: int = 5) -> dict[str, Any]:
        """Papers nearest to this one in embedding space — "more like this".

        Similarity is computed on the full document vectors, not the map's
        3D coordinates, which distort global distance on purpose.
        """
        k = max(1, min(int(k), MAX_SIMILAR))
        status, body = await api.get_json(
            f"/api/graph/similar/{paper_id}", params={"k": k}
        )
        if status == 404:
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ValueError(f"paper {paper_id}: {detail}")
        if status != 200:
            raise ValueError(f"the graph endpoint answered {status}: {body}")
        return {
            "paper_id": paper_id,
            "similar": [
                {
                    "paper_id": hit["id"],
                    "title": hit["title"],
                    "similarity": hit["similarity"],
                }
                for hit in body
            ],
        }

    @server.tool()
    async def list_regions() -> dict[str, Any]:
        """The map's named regions (clusters): id, name, size, top terms.

        A library with no computed map yet answers with a note, not an
        error — that state is normal while the pipeline is still working.
        """
        status, body = await api.get_json("/api/graph")
        if status == 409 and isinstance(body, dict):
            return {"regions": [], "note": body.get("detail", "no map yet")}
        if status != 200:
            raise ValueError(f"the graph endpoint answered {status}: {body}")
        regions = sorted(
            (
                {
                    "id": c["id"],
                    "name": c.get("label"),
                    "size": c.get("size", 0),
                    "terms": (c.get("terms") or [])[:8],
                }
                for c in body.get("clusters", [])
            ),
            key=lambda r: -r["size"],
        )
        return {"papers_on_map": body.get("count"), "regions": regions}

    @server.tool()
    async def region_details(cluster_id: int) -> dict[str, Any]:
        """One region: name, overview, terms, and its most representative
        members (highest cluster confidence first)."""
        status, body = await api.get_json(f"/api/clusters/{cluster_id}")
        if status == 404:
            raise ValueError(f"no region {cluster_id} — list_regions has the ids")
        if status != 200:
            raise ValueError(f"the clusters endpoint answered {status}: {body}")
        members = body.get("members", [])
        return {
            "id": body.get("id"),
            "name": body.get("label"),
            "overview": body.get("overview"),
            "size": body.get("size"),
            "terms": body.get("terms", []),
            "member_count": len(members),
            "members": [
                {
                    "paper_id": m["paper_id"],
                    "title": m.get("title"),
                    "year": m.get("year"),
                    "confidence": m.get("confidence"),
                }
                for m in members[:MAX_REGION_MEMBERS]
            ],
        }

    @server.tool()
    async def library_overview() -> dict[str, Any]:
        """Counts, regions, and tags — the survey to start a session with."""
        status, sys_body = await api.get_json("/api/system")
        overview: dict[str, Any] = {}
        if status == 200 and isinstance(sys_body, dict):
            overview["papers"] = sys_body.get("paper_count")

        gstatus, graph = await api.get_json("/api/graph")
        if gstatus == 200 and isinstance(graph, dict):
            overview["papers_on_map"] = graph.get("count")
            overview["regions"] = sorted(
                (
                    {"id": c["id"], "name": c.get("label"), "size": c.get("size", 0)}
                    for c in graph.get("clusters", [])
                ),
                key=lambda r: -r["size"],
            )
            overview["tags"] = graph.get("tag_vocabulary", [])[:MAX_OVERVIEW_TAGS]
        elif gstatus == 409 and isinstance(graph, dict):
            overview["note"] = graph.get("detail", "no map yet")
        return overview

    return server


def main() -> int:
    build_server().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
