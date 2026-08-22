"""Reviving jobs that died of something since fixed.

`dead` means the queue gave up after three attempts, which is right for a
document nothing can read and wrong for a stage that failed because a library
was missing. All three attempts hit the same ImportError; once the dependency
is installed those jobs are perfectly runnable, and nothing retries them —
being out of retries is exactly what `dead` records.

On the reference corpus this was 449 embed jobs against 506 parsed documents:
the whole library, parsed and then stranded one step short of the map.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from revive_jobs import _cause_of, _unfail_paper  # noqa: E402

from app.models import Paper, PaperStatus  # noqa: E402

MISSING_MODULE = "ModuleNotFoundError: No module named 'sentence_transformers'"
DRM = "UnreadableDocument: DRM-protected (Amazon encrypted container)"


def test_a_missing_library_is_recognised_as_environmental():
    cause = _cause_of(MISSING_MODULE)

    assert cause is not None
    assert cause.module == "sentence_transformers"


@pytest.mark.parametrize(
    "error",
    [
        DRM,
        "UnreadableDocument: no text layer — never run through OCR",
        "RuntimeError: all parse tiers failed",
        "UnicodeEncodeError: 'utf-8' codec can't encode character",
        "ConnectionError: connection refused",
        None,
        "",
    ],
)
def test_everything_else_stays_dead(error):
    """Not a retry-everything button.

    A DRM-locked book that comes back three more times has learned nothing, and
    the queue's verdict on it was correct.
    """
    assert _cause_of(error) is None


def test_the_environment_is_checked_before_reviving():
    """Reviving into an interpreter that still cannot import is 3 wasted tries."""
    cause = _cause_of(MISSING_MODULE)

    installed = importlib.util.find_spec(cause.module) is not None

    assert installed, (
        "sentence-transformers must be a hard dependency: the document vector "
        "is what places a paper on the map, so without it the application's "
        "one feature does not work"
    )


def _paper(db, status: PaperStatus, path: str = "/library/x.pdf") -> Paper:
    paper = Paper(
        content_sha256=status.value.ljust(64, "0")[:64],
        work_key=status.value,
        pdf_path=path,
        pdf_bytes=1,
        status=status,
        last_error="boom",
        pipeline_version=1,
    )
    db.add(paper)
    db.flush()
    return paper


def test_a_failed_paper_gets_its_status_back(db):
    paper = _paper(db, PaperStatus.FAILED)

    _unfail_paper(db, paper.id)

    assert paper.status == PaperStatus.PARSED
    assert paper.last_error is None


def test_a_quarantined_paper_is_left_alone(db):
    """Its file has been moved; `parsed` would point at a path that is gone."""
    paper = _paper(db, PaperStatus.QUARANTINED, "/data/quarantine/20260822/x.pdf")

    _unfail_paper(db, paper.id)

    assert paper.status == PaperStatus.QUARANTINED


def test_a_healthy_paper_is_not_disturbed(db):
    paper = _paper(db, PaperStatus.READY)

    _unfail_paper(db, paper.id)

    assert paper.status == PaperStatus.READY


def test_no_paper_id_is_harmless(db):
    _unfail_paper(db, None)
