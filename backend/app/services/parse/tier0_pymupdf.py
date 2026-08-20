"""Tier 0: read the PDF's embedded text layer.

Costs roughly 0.05 s/page on CPU and handles the large majority of arXiv and
publisher PDFs, which ship a perfectly good text layer. Everything else in the
parse pipeline exists to catch the cases where this is not true.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.services.parse.quality import TextProbe

PARSER_NAME = "pymupdf"
TIER = 0


@dataclass(frozen=True)
class ParseResult:
    markdown: str
    tier: int
    parser: str
    parser_version: str
    page_count: int


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


def probe_pdf(path: Path | str) -> TextProbe:
    """Extract raw text plus the signals the quality gate needs."""
    with pymupdf.open(path) as doc:
        pages = doc.page_count
        text_parts: list[str] = []
        fonts: set[tuple] = set()
        image_ratios: list[float] = []

        for page in doc:
            text_parts.append(page.get_text())
            fonts.update(page.get_fonts())
            image_ratios.append(_image_area_ratio(page))

    return TextProbe(
        text="\n".join(text_parts),
        page_count=pages,
        font_count=len(fonts),
        # Mean rather than max: one scanned figure page in an otherwise digital
        # paper should not send the whole document to the GPU.
        image_area_ratio=(
            sum(image_ratios) / len(image_ratios) if image_ratios else 0.0
        ),
    )


def parse(path: Path | str) -> ParseResult:
    """Convert to Markdown, preserving headings and reading order.

    ``use_ocr=False`` is deliberate and load-bearing. pymupdf4llm will happily
    run Tesseract over pages whose text layer looks thin, which costs ~29% more
    per page and — worse — blurs the tier boundary: it makes the "cheap CPU
    tier" quietly do OCR, so the quality gate can no longer tell the difference
    between a PDF with a good text layer and one that needed rescuing. Optical
    recognition is what tiers 1 and 2 are for.
    """
    import pymupdf4llm

    markdown = pymupdf4llm.to_markdown(str(path), show_progress=False, use_ocr=False)
    with pymupdf.open(path) as doc:
        pages = doc.page_count

    return ParseResult(
        markdown=markdown,
        tier=TIER,
        parser=PARSER_NAME,
        parser_version=pymupdf.__doc__ or "unknown",
        page_count=pages,
    )
