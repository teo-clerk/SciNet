"""Tier 2: a local vision model, one page image at a time.

The last resort, at roughly 8-15 s/page. It exists for documents the other two
tiers cannot read at all — photographed pages, exotic encodings, handwriting —
and is expected to handle around 1% of a normal library. Running a whole corpus
through here would take days, which is exactly why the quality gate is strict
about escalating.

Pages are rendered locally with PyMuPDF and posted to Ollama, so nothing leaves
the machine.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx
import pymupdf

from app.core.config import get_settings
from app.core.model_store import PRIVATE_OLLAMA
from app.services.parse.limits import append_note, page_budget
from app.services.parse.router import ParserUnavailable
from app.services.parse.tier0_pymupdf import ParseResult

logger = logging.getLogger(__name__)

PARSER_NAME = "vlm"
TIER = 2
RENDER_DPI = 150
PAGE_TIMEOUT_SECONDS = 180

# The last two sentences are not padding. Small document VLMs — granite in
# particular — will happily wrap ordinary prose in a Markdown table, which
# survives the quality gate (it is long, spaced and word-like) and then poisons
# the embeddings with pipe characters.
PROMPT = (
    "Transcribe this page of a document into clean Markdown. "
    "Preserve headings, paragraph structure and lists. "
    "Render equations as LaTeX between $ delimiters. "
    "Use a Markdown table ONLY where the page itself shows a real table with "
    "rows and columns; write ordinary paragraphs as plain prose, never as a "
    "table. "
    "Do not summarise, do not add commentary, and do not invent content that "
    "is not visible on the page. Output only the Markdown."
)


def available(client: httpx.Client | None = None) -> bool:
    """True when the project's own store has the vision model and can serve it.

    Checks the private server, never the system-wide one: a model installed
    globally is not a model this project may rely on.
    """
    settings = get_settings()
    try:
        PRIVATE_OLLAMA.start()
    except Exception:  # noqa: BLE001 - no binary, or it would not come up
        return False

    try:
        owned = client is None
        c = client or httpx.Client(timeout=5.0)
        try:
            resp = c.get(f"{settings.ollama_url}/api/tags")
            resp.raise_for_status()
            names = {m.get("name", "") for m in resp.json().get("models", [])}
            return settings.vlm_model in names
        finally:
            if owned:
                c.close()
    except Exception:  # noqa: BLE001 - unreachable server is just "unavailable"
        return False


def unload() -> None:
    """Ask Ollama to drop the model immediately.

    Without this the model keeps its ~6 GiB resident and the next stage OOMs on
    a card this size — a failure that surfaces as an opaque CUDA error.
    """
    settings = get_settings()
    try:
        httpx.post(
            f"{settings.ollama_url}/api/generate",
            json={"model": settings.vlm_model, "keep_alive": 0},
            timeout=30.0,
        )
    except Exception:  # noqa: BLE001
        logger.warning("could not unload %s from ollama", settings.vlm_model)


def _render_page(page: pymupdf.Page, dpi: int = RENDER_DPI) -> str:
    pixmap = page.get_pixmap(dpi=dpi)
    return base64.b64encode(pixmap.tobytes("png")).decode("ascii")


def _transcribe(client: httpx.Client, image_b64: str) -> str:
    settings = get_settings()
    response = client.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": settings.vlm_model,
            "prompt": PROMPT,
            "images": [image_b64],
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0.0, "num_ctx": settings.llm_num_ctx},
        },
        timeout=PAGE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def parse(path: Path | str, *, limit: int | None = None) -> ParseResult:
    settings = get_settings()
    if limit is None:
        limit = settings.max_parse_pages
    if not available():
        raise ParserUnavailable(
            f"{settings.vlm_model} is not in the project's model store; "
            f"run scripts/download_models.py"
        )

    pages_markdown: list[str] = []
    with (
        pymupdf.open(path) as doc,
        httpx.Client(timeout=PAGE_TIMEOUT_SECONDS) as client,
    ):
        total = doc.page_count
        budget = page_budget(total, limit)
        if budget < total:
            # The single most valuable line in this file. At 8-15 s/page a
            # 731-page scan is hours of GPU; one such job was killed after
            # seven of them, still unfinished, with 435 documents behind it.
            logger.info(
                "%s is %d pages; transcribing the first %d",
                Path(path).name,
                total,
                budget,
            )

        for index in range(1, budget + 1):
            logger.info(
                "vlm transcribing page %d/%d of %s", index, budget, Path(path).name
            )
            try:
                pages_markdown.append(_transcribe(client, _render_page(doc[index - 1])))
            except Exception as exc:  # noqa: BLE001
                # One unreadable page must not discard the rest of the paper.
                logger.warning("vlm failed on page %d: %s", index, exc)
                pages_markdown.append(f"<!-- page {index}: transcription failed -->")

    return ParseResult(
        markdown=append_note("\n\n".join(pages_markdown), kept=budget, total=total),
        tier=TIER,
        parser=PARSER_NAME,
        parser_version=settings.vlm_model,
        page_count=total,
        pages_parsed=budget,
    )
