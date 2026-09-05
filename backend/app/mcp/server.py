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

import re
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
#: get_curriculum on a topic ranks this many semantic hits — enough for a
#: real reading order, few enough that a vague phrase does not rank the
#: whole library.
MAX_CURRICULUM_HITS = 40
DEFAULT_QUANTITY_ROWS = 20
MAX_QUANTITY_ROWS = 100

INSTRUCTIONS = (
    "SciNet is the user's local, private library of papers, books and essays "
    "from any discipline, already parsed, embedded, and clustered into a "
    "semantic map. "
    "Use search_library to find papers (semantic for meaning, fulltext for "
    "exact words, title for names you already know), then get_paper for "
    "metadata and the abstract. Paper ids are stable integers. "
    "find_semantic_path walks the library from one idea to another; "
    "get_curriculum says where to start reading a region or a topic and in "
    "what order; query_quantities finds measured values with the sentence "
    "each came from."
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


def _path_end(name: str, concept: str) -> dict[str, str]:
    """One end of a trail: digits (or #digits) are a paper id, else a phrase."""
    concept = concept.strip()
    if concept.startswith("#") and concept[1:].isdigit():
        concept = concept[1:]
    if concept.isdigit():
        return {name: concept}
    if len(concept) < 2:
        raise ValueError(
            f"{name}_concept needs a paper id or a phrase of two or more characters"
        )
    return {f"{name}_text": concept}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _region_matches(wanted: str, label: str | None) -> bool:
    """A region name matches when its words are all in the ask, or vice
    versa — "active inference" finds Active Inference Theory, and a stray
    "ai" inside "Brain" does not."""
    if not label:
        return False
    asked, named = _words(wanted), _words(label)
    return bool(asked) and (asked <= named or named <= asked)


def _entry(point: dict[str, Any] | None) -> dict[str, Any] | None:
    if not point:
        return None
    return {
        "paper_id": point["paper_id"],
        "title": point.get("title"),
        "year": point.get("year"),
        "reasons": point.get("reasons", []),
    }


def _curriculum_answer(
    resolved_as: str,
    *,
    region: dict[str, Any] | None,
    topic: str | None,
    body: dict[str, Any],
    titles: dict[int, dict[str, Any]],
    considered: int,
) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "resolved_as": resolved_as,
        "region": region,
        "topic": topic,
        "considered": considered,
        "entry_point": _entry(body.get("entry_point")),
        "alternatives": [_entry(a) for a in body.get("alternatives", [])],
        "reading_order": [
            {
                "position": i + 1,
                "paper_id": pid,
                "title": titles.get(pid, {}).get("title"),
                "year": titles.get(pid, {}).get("year"),
            }
            for i, pid in enumerate(body.get("reading_order", []))
        ],
    }
    if answer["entry_point"] is None:
        answer["note"] = "too few embedded works here to rank — it takes at least two"
    return answer


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
    async def find_semantic_path(from_concept: str, to_concept: str) -> dict[str, Any]:
        """The chain of works leading from one idea to another through this
        library, each stop a small step from the last.

        Each end is a phrase ("Stoic ethics") or a paper id as digits
        ("142"). Stops carry the work's central question and its region;
        regions_crossed lists the regions in order. complete=false means the
        library holds no continuous chain and the last step is a jump — say
        so rather than smoothing it over.
        """
        params = {**_path_end("from", from_concept), **_path_end("to", to_concept)}
        status, body = await api.get_json("/api/graph/path", params=params)
        if status == 503 and isinstance(body, dict):
            return _warming_answer(body.get("detail", {}))
        if status in (400, 404):
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ValueError(f"no trail: {detail}")
        if status != 200:
            raise ValueError(f"the path endpoint answered {status}: {body}")
        stops = [
            {
                "position": i + 1,
                "paper_id": stop["paper_id"],
                "title": stop.get("title"),
                "year": stop.get("year"),
                "region": stop.get("cluster_label"),
                "core_question": stop.get("core_question"),
                "similarity_to_next": stop.get("similarity_to_next"),
            }
            for i, stop in enumerate(body.get("stops", []))
        ]
        regions: list[str] = []
        for stop in stops:
            region = stop["region"]
            if region and (not regions or regions[-1] != region):
                regions.append(region)
        answer: dict[str, Any] = {
            "from": body.get("from"),
            "to": body.get("to"),
            "complete": body.get("complete"),
            "hops": body.get("hops"),
            "thinned": body.get("thinned"),
            "regions_crossed": regions,
            "stops": stops,
        }
        if not body.get("complete", True):
            answer["note"] = (
                "the library has no continuous chain between these two — "
                "the last step is a jump, not a neighbour"
            )
        return answer

    @server.tool()
    async def query_quantities(
        quantity_kind: str | None = None,
        unit: str | None = None,
        min_val: float | None = None,
        max_val: float | None = None,
        query: str | None = None,
        limit: int = DEFAULT_QUANTITY_ROWS,
    ) -> dict[str, Any]:
        """Measured values across the library, each with the sentence it
        came from and the paper it is in.

        Filter by kind ("frequency", "length" — call with nothing to see the
        kinds this library holds), by unit ("kHz" narrows to values written
        that way; "hertz" selects the kind), by an SI range (min_val and
        max_val are in the kind's SI unit — each row says which in unit_si),
        or by a phrase matched against the sentence. Trusted values only:
        extracted with a known unit or confirmed by the reader, never what
        the adjudicator was unsure about.
        """
        limit = max(1, min(int(limit), MAX_QUANTITY_ROWS))
        if quantity_kind is None and unit is None and query is None:
            status, body = await api.get_json("/api/quantities/kinds")
            if status != 200:
                raise ValueError(f"the quantities endpoint answered {status}: {body}")
            return {
                "kinds": [
                    {
                        "kind": k["kind"],
                        "unit_si": k.get("unit_si"),
                        "count": k["count"],
                        "min_value": k["min_value"],
                        "max_value": k["max_value"],
                    }
                    for k in body
                ],
                "note": "give a kind, a unit or a phrase to see the rows",
            }
        params: dict[str, Any] = {"limit": limit}
        if quantity_kind is not None:
            params["kind"] = quantity_kind
        if unit is not None:
            params["unit"] = unit
        if min_val is not None:
            params["min_value"] = min_val
        if max_val is not None:
            params["max_value"] = max_val
        if query is not None:
            params["q"] = query
        status, body = await api.get_json("/api/quantities/rows", params=params)
        if status in (400, 422):
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ValueError(f"quantities: {detail}")
        if status != 200:
            raise ValueError(f"the quantities endpoint answered {status}: {body}")
        rows = body.get("rows", [])
        return {
            "count": body.get("count", 0),
            "papers": body.get("papers", 0),
            "returned": len(rows),
            "rows": [
                {
                    "paper_id": r["paper_id"],
                    "paper_title": r.get("paper_title"),
                    "kind": r["quantity_kind"],
                    "value_si": r["value_si"],
                    "unit_si": r.get("unit_si"),
                    "value_original": r["value_original"],
                    "unit_original": r["unit_original"],
                    "sentence": r["context_sentence"],
                    "section": r.get("section"),
                    "status": r["status"],
                }
                for r in rows
            ],
        }

    async def _match_region(wanted: str) -> dict[str, Any] | None:
        """The region a name points at, largest first; None means "a topic"."""
        status, body = await api.get_json("/api/graph")
        if status != 200 or not isinstance(body, dict):
            return None  # no map yet — a topic can still be ranked
        matches = [
            c
            for c in body.get("clusters", [])
            if _region_matches(wanted, c.get("label"))
        ]
        if not matches:
            return None
        best = max(matches, key=lambda c: c.get("size", 0))
        return {
            "id": best["id"],
            "name": best.get("label"),
            "size": best.get("size", 0),
        }

    @server.tool()
    async def get_curriculum(topic_or_cluster: str) -> dict[str, Any]:
        """Where to start reading a region or a topic, and in what order.

        Name a region as list_regions gives it, or describe a topic; a
        region wins when the name matches. The entry point is the work
        nearest the centre, nudged toward anything that reads as an
        introduction and toward the earlier, shorter works — each reason is
        stated. reading_order walks the rest, each step the nearest unread
        work to the last.
        """
        wanted = topic_or_cluster.strip()
        if len(wanted) < 2:
            raise ValueError(
                "name a region or describe a topic (two characters or more)"
            )

        region = await _match_region(wanted)
        if region is not None:
            status, body = await api.get_json(f"/api/clusters/{region['id']}")
            if status != 200:
                raise ValueError(f"the clusters endpoint answered {status}: {body}")
            members = body.get("members", [])
            return _curriculum_answer(
                "region",
                region=region,
                topic=None,
                body=body,
                titles={m["paper_id"]: m for m in members},
                considered=len(members),
            )

        status, found = await api.get_json(
            "/api/search/semantic", params={"q": wanted, "limit": MAX_CURRICULUM_HITS}
        )
        if status == 503 and isinstance(found, dict):
            return _warming_answer(found.get("detail", {}))
        if status != 200:
            raise ValueError(f"search answered {status}: {found}")
        hits = found.get("hits", [])
        if not hits:
            return {
                "resolved_as": "topic",
                "region": None,
                "topic": wanted,
                "considered": 0,
                "entry_point": None,
                "alternatives": [],
                "reading_order": [],
                "note": "nothing in the library matches this topic",
            }
        status, body = await api.post_json(
            "/api/graph/entry-point", {"paper_ids": [h["paper_id"] for h in hits]}
        )
        if status != 200:
            raise ValueError(f"the entry-point endpoint answered {status}: {body}")
        return _curriculum_answer(
            "topic",
            region=None,
            topic=wanted,
            body=body,
            titles={h["paper_id"]: h for h in hits},
            considered=int(body.get("considered", 0)),
        )

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
