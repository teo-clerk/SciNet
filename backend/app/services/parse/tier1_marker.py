"""Tier 1: Marker + Surya on the GPU.

The strongest batch option for multi-column academic layouts, at roughly
1-3 s/page on this hardware. Imported lazily and behind ``ParserUnavailable``
because it is a heavy optional dependency (``uv sync --extra gpu``) and a
machine without it should still ingest papers, just with more escalations to
tier 2.

Licensing note: Marker is GPL-3.0 with RAIL-M weights carrying a revenue
threshold. Fine for personal use, a blocker for redistribution — which is why
it sits behind the same ``Parser`` protocol as everything else, so Docling
(MIT) or MinerU can replace it by editing this file alone.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.model_store import configure_environment
from app.services.parse.router import ParserUnavailable
from app.services.parse.tier0_pymupdf import ParseResult

logger = logging.getLogger(__name__)

PARSER_NAME = "marker"
TIER = 1


@lru_cache(maxsize=1)
def _converter():
    """Load Marker once and keep it. Reloading costs 5-15 s per call."""
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ParserUnavailable(
            "marker-pdf is not installed; run `uv sync --extra gpu`"
        ) from exc

    settings = get_settings()
    # Point Surya/transformers at the project's cache before the first import
    # pulls weights, so they land in data/models/hf rather than ~/.cache.
    configure_environment(settings)
    logger.info("loading marker models onto %s", settings.device)
    return PdfConverter(artifact_dict=create_model_dict(device=settings.device))


def unload() -> None:
    """Release VRAM so the next stage's model can fit.

    Surya 2 runs inference in a *separate* llama-server process, so clearing
    the Python-side cache frees almost nothing — the several GiB that matter
    belong to that child. It has to be stopped explicitly, or it sits on the
    card while the next stage tries to load a 5.5 GiB model onto the same
    8 GiB device.
    """
    _converter.cache_clear()

    try:
        from surya.inference import get_default_manager

        manager = get_default_manager()
        if manager is not None:
            manager.stop()
            logger.info("stopped the surya inference server")
    except Exception as exc:  # noqa: BLE001 - never block the next stage
        logger.debug("could not stop the surya server cleanly: %s", exc)

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def available() -> bool:
    try:
        import marker  # noqa: F401
    except ImportError:
        return False
    return True


def parse(path: Path | str) -> ParseResult:
    try:
        from marker.output import text_from_rendered
    except ImportError as exc:
        raise ParserUnavailable("marker-pdf is not installed") from exc

    rendered = _converter()(str(path))
    markdown, _metadata, _images = text_from_rendered(rendered)

    import pymupdf

    with pymupdf.open(path) as doc:
        pages = doc.page_count

    return ParseResult(
        markdown=markdown,
        tier=TIER,
        parser=PARSER_NAME,
        parser_version=_marker_version(),
        page_count=pages,
    )


def _marker_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("marker-pdf")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"
