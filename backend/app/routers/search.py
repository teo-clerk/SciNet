"""Search, in three modes that answer different questions.

*Full text* finds papers that contain a phrase. It is exact, fast, and cannot
find a paper that discusses an idea in other words.

*Semantic* finds papers that mean something similar, including ones sharing no
vocabulary with the query. It cannot tell you where in the paper the match is,
and it will always return its k nearest results however unrelated they are.

*Title* matching happens in the browser against the payload it already has, so
it costs no round trip. It lives in the frontend, not here.

The two server modes are kept separate rather than blended into one ranked
list: a fused score would hide which kind of match produced a hit, and the
distinction is exactly what tells the reader whether to trust it.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db

router = APIRouter(prefix="/api/search", tags=["search"])

MAX_SNIPPET_CHARS = 240
# FTS5 treats these as operators; a reader typing them means them literally.
FTS_SPECIAL = re.compile(r'["\^\*\(\):]')


class SearchHit(BaseModel):
    paper_id: int
    title: str | None
    score: float
    #: Where the match was found — a chunk of the paper for full text, the
    #: whole document for semantic.
    snippet: str | None = None
    section: str | None = None


class SearchResponse(BaseModel):
    mode: str
    query: str
    hits: list[SearchHit]


def _sanitise_fts(query: str) -> str:
    """Make a user's words safe to hand to FTS5's query parser.

    Each term becomes a prefix match so partial words behave the way a reader
    expects from a search box. Quoting each term individually keeps FTS5 from
    interpreting stray punctuation as syntax and returning a 500 on an
    apostrophe.
    """
    terms = [t for t in FTS_SPECIAL.sub(" ", query).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


@router.get("/fulltext", response_model=SearchResponse)
def search_fulltext(
    q: str = Query(..., min_length=2),
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Exact phrase and term search over the parsed Markdown."""
    expression = _sanitise_fts(q)
    if not expression:
        return SearchResponse(mode="fulltext", query=q, hits=[])

    # bm25() is negative-better in SQLite, so it is negated for a score that
    # sorts and reads the same way as the semantic one.
    statement = text(
        """
        SELECT c.paper_id            AS paper_id,
               pm.title              AS title,
               -bm25(chunks_fts)     AS score,
               snippet(chunks_fts, 0, '', '', '…', 24) AS snippet,
               c.section             AS section
          FROM chunks_fts
          JOIN chunks    c  ON c.id = chunks_fts.rowid
          LEFT JOIN paper_meta pm ON pm.paper_id = c.paper_id
         WHERE chunks_fts MATCH :expression
         ORDER BY bm25(chunks_fts)
         LIMIT :limit
        """
    ).bindparams(bindparam("expression"), bindparam("limit"))

    try:
        rows = db.execute(
            statement, {"expression": expression, "limit": limit * 3}
        ).all()
    except Exception as exc:  # noqa: BLE001 - a malformed query is the user's, not a fault
        raise HTTPException(400, f"could not parse that query: {exc}") from exc

    # One hit per paper: ten chunks of the same paper is not ten results.
    best: dict[int, SearchHit] = {}
    for row in rows:
        if row.paper_id in best:
            continue
        best[row.paper_id] = SearchHit(
            paper_id=row.paper_id,
            title=row.title,
            score=round(float(row.score), 4),
            snippet=(row.snippet or "")[:MAX_SNIPPET_CHARS] or None,
            section=row.section,
        )
        if len(best) >= limit:
            break

    return SearchResponse(mode="fulltext", query=q, hits=list(best.values()))


@router.get("/semantic", response_model=SearchResponse)
def search_semantic(
    q: str = Query(..., min_length=2),
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SearchResponse:
    """Meaning-based search over the document vectors.

    Runs in the 1024-D embedding space, never on the rendered coordinates —
    UMAP distorts global distance deliberately, so the projection would answer
    a different question.
    """
    from app.services.embed import encoder
    from app.workers.embed_handlers import open_store

    try:
        vector = encoder.encode_query(q, settings=settings)
    except Exception as exc:  # noqa: BLE001 - model absent or not yet downloaded
        raise HTTPException(503, f"the embedding model is unavailable: {exc}") from exc

    store = open_store(settings)
    if store.count == 0:
        return SearchResponse(mode="semantic", query=q, hits=[])

    matches = store.nearest(vector, k=limit)
    titles = dict(
        db.execute(
            text(
                "SELECT paper_id, title FROM paper_meta WHERE paper_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": [pid for pid, _ in matches]},
        ).all()
    )
    return SearchResponse(
        mode="semantic",
        query=q,
        hits=[
            SearchHit(paper_id=pid, title=titles.get(pid), score=round(score, 4))
            for pid, score in matches
        ],
    )
