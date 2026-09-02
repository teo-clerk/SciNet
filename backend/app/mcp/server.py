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

import os
from collections.abc import Callable
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.mcp.client import ApiClient

SEARCH_MODES = ("semantic", "fulltext", "title")
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 40

INSTRUCTIONS = (
    "SciNet is the user's local, private library of scientific papers and "
    "books, already parsed, embedded, and clustered into a semantic map. "
    "Use search_library to find papers (semantic for meaning, fulltext for "
    "exact words, title for names you already know), then get_paper for "
    "metadata and the abstract. Paper ids are stable integers."
)


def default_base_url() -> str:
    """SCINET_API_URL wins; otherwise the configured API port.

    Importing settings lazily keeps `--help` and tests from touching the
    repository's .env before they mean to.
    """
    env = os.environ.get("SCINET_API_URL")
    if env:
        return env
    from app.core.config import get_settings

    return f"http://127.0.0.1:{get_settings().port}"


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

    return server


def main() -> int:
    build_server().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
