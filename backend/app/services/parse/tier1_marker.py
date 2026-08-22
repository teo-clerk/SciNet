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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.model_store import configure_environment
from app.services.parse.limits import append_note, page_budget
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

    # Surya spawns a *second* server for OCR error detection, which the default
    # manager does not own and therefore does not stop. One was found alive
    # hours after its worker exited, still holding 800 MiB of an 8 GiB card and
    # starving everything that came after it.
    try:
        _stop_orphaned_surya_servers()
    except Exception as exc:  # noqa: BLE001 - cleanup must not block the card
        logger.debug("orphan scan skipped: %s", exc)

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _stop_orphaned_surya_servers() -> None:
    """Terminate surya helper processes this machine left behind.

    Matching on the module path rather than a broad pattern: these are
    identifiable and nothing else on the system looks like them.
    """
    import os
    import signal
    from pathlib import Path as _Path

    # /proc is Linux-only. On Windows and macOS this scan cannot run, and an
    # unguarded iterdir() would raise straight past the torch cache release
    # below it — leaving VRAM held on exactly the platforms that cannot use
    # this cleanup in the first place.
    procfs = _Path("/proc")
    if not procfs.is_dir():
        logger.debug("no procfs; skipping the orphaned-helper scan")
        return

    markers = ("surya.ocr_error", "surya.inference", "surya.scripts")
    for entry in procfs.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode(errors="ignore")
        except OSError:
            continue
        if not any(marker in cmdline for marker in markers):
            continue
        if "python" not in cmdline:
            continue
        try:
            os.kill(int(entry.name), signal.SIGTERM)
            logger.info("stopped orphaned surya helper pid %s", entry.name)
        except OSError:
            pass


def available() -> bool:
    if not get_settings().tier1_enabled:
        return False
    try:
        import marker  # noqa: F401
    except ImportError:
        return False
    return True


class Tier1Timeout(TimeoutError):
    """Tier 1 exceeded its wall-clock budget for one document."""


def _convert(path: str, page_range: list[int] | None = None) -> str:
    from marker.output import text_from_rendered

    converter = _converter()
    if page_range is not None:
        # Marker reads page_range off its own config rather than the call, so
        # the limit has to be set on the converter it already built. Restored
        # afterwards because the converter is cached and reused for the next
        # document, which will have a different length.
        previous = converter.config.get("page_range")
        converter.config["page_range"] = page_range
        try:
            rendered = converter(path)
        finally:
            converter.config["page_range"] = previous
    else:
        rendered = converter(path)
    markdown, _metadata, _images = text_from_rendered(rendered)
    return markdown


def parse(path: Path | str, *, limit: int | None = None) -> ParseResult:
    settings = get_settings()
    if not settings.tier1_enabled:
        raise ParserUnavailable(
            "tier 1 is disabled (SCINET_TIER1_ENABLED=false); "
            "escalating papers go straight to tier 2"
        )

    try:
        import marker.output  # noqa: F401
    except ImportError as exc:
        raise ParserUnavailable("marker-pdf is not installed") from exc

    import pymupdf

    with pymupdf.open(path) as doc:
        pages = doc.page_count
    kept = page_budget(pages, settings.max_parse_pages if limit is None else limit)
    page_range = list(range(kept)) if kept < pages else None

    budget = settings.tier1_timeout_seconds * settings.tier1_calls_per_document

    # SURYA_INFERENCE_TIMEOUT_SECONDS bounds a single inference call, but Marker
    # issues several per document (layout, recognition, tables), so per-call
    # limits do not bound the document. A degenerate page can therefore still
    # occupy the GPU for many minutes under a 90 s per-call limit. This is the
    # bound that actually holds.
    #
    # The worker thread is left running rather than killed — Python cannot
    # interrupt a blocking C call — but it is a daemon, and the router treats
    # the timeout as "fall through", so the paper still gets read by tier 2.
    # Deliberately NOT a `with` block. ThreadPoolExecutor.__exit__ calls
    # shutdown(wait=True), which blocks until the worker thread finishes — the
    # very thread this timeout exists to walk away from. Using the context
    # manager here makes the timeout a no-op that merely changes which
    # exception is raised, hours later.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tier1")
    try:
        future = pool.submit(_convert, str(path), page_range)
        try:
            markdown = future.result(timeout=budget)
        except FuturesTimeout as exc:
            raise Tier1Timeout(
                f"tier 1 exceeded its {budget:.0f}s budget on {Path(path).name}"
            ) from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return ParseResult(
        markdown=append_note(markdown, kept=kept, total=pages),
        tier=TIER,
        parser=PARSER_NAME,
        parser_version=_marker_version(),
        page_count=pages,
        pages_parsed=kept,
    )


def _marker_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("marker-pdf")
    except PackageNotFoundError:  # pragma: no cover
        return "unknown"
