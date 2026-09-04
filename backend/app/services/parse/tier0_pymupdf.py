"""Tier 0: read the PDF's embedded text layer.

Costs roughly 0.05 s/page on CPU and handles the large majority of arXiv and
publisher PDFs, which ship a perfectly good text layer. Everything else in the
parse pipeline exists to catch the cases where this is not true.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.core.config import get_settings
from app.services.parse.limits import (
    MAX_PAGE_PATHS,
    append_note,
    append_plain_pages_note,
    page_budget,
)
from app.services.parse.quality import TextProbe

PARSER_NAME = "pymupdf"
TIER = 0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseResult:
    markdown: str
    tier: int
    parser: str
    parser_version: str
    #: What the document really is, not what was read. A truncated book is
    #: still a 731-page book, and the sidebar should say so.
    page_count: int
    #: Pages actually converted. Equal to page_count unless the limit applied.
    pages_parsed: int | None = None
    #: Pages (1-based) within the budget read as plain text, without layout,
    #: because their vector graphics would have cost hours. Distinct from
    #: truncation: the document was read to its end.
    pages_plain: tuple[int, ...] = ()

    @property
    def truncated(self) -> bool:
        return self.pages_parsed is not None and self.pages_parsed < self.page_count


def dense_pages(doc: pymupdf.Document, budget: int) -> tuple[int, ...]:
    """0-based pages with too many vector paths to lay out in bounded time."""
    return tuple(
        index
        for index in range(min(budget, doc.page_count))
        if len(doc[index].get_cdrawings()) > MAX_PAGE_PATHS
    )


def _segments(budget: int, dense: tuple[int, ...]) -> list[tuple[bool, list[int]]]:
    """Consecutive pages grouped by how they are read: (is_plain, pages)."""
    segments: list[tuple[bool, list[int]]] = []
    for index in range(budget):
        plain = index in dense
        if segments and segments[-1][0] == plain:
            segments[-1][1].append(index)
        else:
            segments.append((plain, [index]))
    return segments


def _image_area_ratio(page: pymupdf.Page) -> float:
    """Fraction of the page covered by raster images, clamped to 1.0.

    A page that is essentially one big scan has no usable text layer even when
    a stray caption extracts cleanly.
    """
    page_area = abs(page.rect.get_area())
    if page_area <= 0:
        return 0.0

    covered = 0.0
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:  # image block
            covered += abs(pymupdf.Rect(block["bbox"]).get_area())
    return min(covered / page_area, 1.0)


def probe_pdf(path: Path | str, *, limit: int | None = None) -> TextProbe:
    """Extract raw text plus the signals the quality gate needs.

    Probes only as far as the parse would go. That is not merely a saving: the
    gate decides which tier reads the document, and judging it on pages nobody
    will read makes the decision answer a different question than the one being
    asked. A book whose first eighty pages are clean typeset text should be
    read by tier 0 even if it ends in two hundred scanned plates.
    """
    if limit is None:
        limit = get_settings().max_parse_pages

    with pymupdf.open(path) as doc:
        pages = doc.page_count
        budget = page_budget(pages, limit)
        text_parts: list[str] = []
        fonts: set[tuple] = set()
        image_ratios: list[float] = []

        for index in range(budget):
            page = doc[index]
            text_parts.append(page.get_text())
            fonts.update(page.get_fonts())
            image_ratios.append(_image_area_ratio(page))

    return TextProbe(
        text="\n".join(text_parts),
        page_count=pages,
        pages_sampled=budget,
        font_count=len(fonts),
        # Mean rather than max: one scanned figure page in an otherwise digital
        # paper should not send the whole document to the GPU.
        image_area_ratio=(
            sum(image_ratios) / len(image_ratios) if image_ratios else 0.0
        ),
    )


def parse(path: Path | str, *, limit: int | None = None) -> ParseResult:
    """Convert to Markdown, preserving headings and reading order.

    ``use_ocr=False`` is deliberate and load-bearing. pymupdf4llm will happily
    run Tesseract over pages whose text layer looks thin, which costs ~29% more
    per page and — worse — blurs the tier boundary: it makes the "cheap CPU
    tier" quietly do OCR, so the quality gate can no longer tell the difference
    between a PDF with a good text layer and one that needed rescuing. Optical
    recognition is what tiers 1 and 2 are for.
    """
    import pymupdf4llm

    if limit is None:
        limit = get_settings().max_parse_pages

    with pymupdf.open(path) as doc:
        pages = doc.page_count
        budget = page_budget(pages, limit)
        dense = dense_pages(doc, budget)
        if dense:
            logger.warning(
                "%s: reading page(s) %s as plain text, vector graphics too dense",
                Path(path).name,
                ", ".join(str(i + 1) for i in dense),
            )
        parts: list[str] = []
        for plain, run in _segments(budget, dense):
            if plain:
                parts.append("\n\n".join(doc[index].get_text() for index in run))
            else:
                # The one-call, whole-document form when nothing is dense and
                # nothing is cut — the ordinary paper's path, unchanged.
                parts.append(
                    pymupdf4llm.to_markdown(
                        str(path),
                        pages=run if len(run) < pages else None,
                        show_progress=False,
                        use_ocr=False,
                    )
                )
    markdown = append_note("\n\n".join(parts), kept=budget, total=pages)
    plain_pages = tuple(index + 1 for index in dense)

    return ParseResult(
        markdown=append_plain_pages_note(markdown, pages=plain_pages),
        tier=TIER,
        parser=PARSER_NAME,
        parser_version=pymupdf.__doc__ or "unknown",
        page_count=pages,
        pages_parsed=budget,
        pages_plain=plain_pages,
    )
