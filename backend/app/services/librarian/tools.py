"""What the librarian can actually do: search, relate, read.

Every tool returns its evidence — the paper ids its result rests on —
because the citation gate downstream only admits ids the tools produced.
An answer can then cite nothing the retrieval never saw, structurally.

Sync on purpose: these run under ``anyio.to_thread.run_sync`` from the
endpoint's async generator, and the underlying machinery (SQLite, the
memmap store, a CPU encoder) is all blocking work that belongs on a thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MarkdownDoc, PaperMeta

MAX_HITS = 8
SNIPPET_CHARS = 220
READ_WINDOW = 3000


@dataclass(frozen=True)
class ToolResult:
    tool: str
    summary: str
    paper_ids: tuple[int, ...] = field(default_factory=tuple)


def _titles(session: Session, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = session.execute(
        select(PaperMeta.paper_id, PaperMeta.title).where(PaperMeta.paper_id.in_(ids))
    ).all()
    return {pid: title or f"paper {pid}" for pid, title in rows}


def search_fulltext(session: Session, query: str, limit: int = MAX_HITS) -> ToolResult:
    """BM25 over the chunk index — exact words, with the matching snippet."""
    # The sanitiser lives with the search router; reusing it beats a second,
    # subtly different quoting of FTS5's operator characters.
    from app.routers.search import _sanitise_fts

    expression = _sanitise_fts(query)
    if not expression:
        return ToolResult("search_fulltext", "the query held no searchable terms")

    statement = text(
        """
        SELECT c.paper_id AS paper_id,
               pm.title AS title,
               snippet(chunks_fts, 0, '', '', '…', 24) AS snippet
          FROM chunks_fts
          JOIN chunks c ON c.id = chunks_fts.rowid
          LEFT JOIN paper_meta pm ON pm.paper_id = c.paper_id
         WHERE chunks_fts MATCH :expression
         ORDER BY bm25(chunks_fts)
         LIMIT :limit
        """
    ).bindparams(bindparam("expression"), bindparam("limit"))

    seen: dict[int, str] = {}
    for row in session.execute(
        statement, {"expression": expression, "limit": limit * 3}
    ):
        if row.paper_id not in seen:
            snippet = " ".join((row.snippet or "").split())[:SNIPPET_CHARS]
            seen[row.paper_id] = f"[#{row.paper_id}] {row.title}: {snippet}"
        if len(seen) >= limit:
            break

    if not seen:
        return ToolResult("search_fulltext", f"no full-text matches for {query!r}")
    return ToolResult("search_fulltext", "\n".join(seen.values()), tuple(seen.keys()))


def semantic_ready() -> bool:
    from app.core.warmup import WARMER

    return WARMER.is_ready


def search_semantic(
    session: Session, settings: Settings, query: str, limit: int = MAX_HITS
) -> ToolResult:
    """Nearest documents by meaning. Caller must gate on semantic_ready()."""
    from app.services.embed import encoder
    from app.workers.embed_handlers import open_store

    vector = encoder.encode_query(query, settings=settings)
    store = open_store(settings)
    if store.count == 0:
        return ToolResult("search_semantic", "the library holds no vectors yet")

    hits = store.nearest(vector, k=limit)
    titles = _titles(session, [pid for pid, _ in hits])
    lines = [
        f"[#{pid}] {titles.get(pid, f'paper {pid}')} (similarity {score:.2f})"
        for pid, score in hits
    ]
    return ToolResult(
        "search_semantic", "\n".join(lines), tuple(pid for pid, _ in hits)
    )


def similar(
    session: Session, settings: Settings, paper_id: int, k: int = 6
) -> ToolResult:
    from app.workers.embed_handlers import open_store

    store = open_store(settings)
    query = store.get(paper_id)
    if query is None:
        return ToolResult("similar", f"paper {paper_id} has no embedding yet")
    hits = store.nearest(query, k=k, exclude={paper_id})
    titles = _titles(session, [pid for pid, _ in hits])
    lines = [
        f"[#{pid}] {titles.get(pid, f'paper {pid}')} (similarity {score:.2f})"
        for pid, score in hits
    ]
    return ToolResult("similar", "\n".join(lines), tuple(pid for pid, _ in hits))


def read_window(
    session: Session, paper_id: int, offset: int = 0, window: int = READ_WINDOW
) -> ToolResult:
    doc = session.get(MarkdownDoc, paper_id)
    if doc is None:
        return ToolResult("read", f"paper {paper_id} has not been parsed yet")
    path = Path(doc.md_path)
    if not path.exists():
        return ToolResult("read", f"paper {paper_id}'s markdown is missing from disk")
    body = path.read_text(encoding="utf-8")
    piece = body[max(0, offset) : max(0, offset) + window]
    header = f"[#{paper_id}] chars {offset}-{offset + len(piece)} of {len(body)}:\n"
    return ToolResult("read", header + piece, (paper_id,))
