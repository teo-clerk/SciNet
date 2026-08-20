"""End-to-end: a PDF on disk becomes a parsed, described paper row.

Exercises the real registrar, the real queue, the real worker handlers and the
real tier-0 parser. Only the GPU tiers are stubbed, because a test suite must
not depend on Marker being installed or Ollama being up.
"""

from __future__ import annotations

import json
import shutil

import pytest

from app.core.config import Settings
from app.models import (
    Job,
    JobKind,
    JobState,
    MarkdownDoc,
    Paper,
    PaperMeta,
    PaperStatus,
)
from app.services.ingest.registrar import Registration, register_pdf
from app.workers.handlers import handle_metadata, handle_parse
from app.workers.queue import claim_next, complete


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "t.db",
        enrichment_enabled=False,
    )


@pytest.fixture
def library(settings, pdf_fixtures):
    settings.ensure_dirs()
    placed = {}
    for name in ("with_identifiers.pdf", "clean_single_column.pdf"):
        dest = settings.library_dir / name
        shutil.copy(pdf_fixtures[name], dest)
        placed[name] = dest
    return placed


def run_stage(sf, kind: JobKind, settings: Settings, handler) -> int:
    """Drain one stage the way the worker does."""
    done = 0
    while True:
        with sf() as session:
            job = claim_next(session, kinds=[kind])
            if job is None:
                return done
            handler(session, job, settings)
            complete(session, job)
            session.commit()
            done += 1


def test_pdf_becomes_a_parsed_paper(sf, settings, library):
    with sf() as s:
        result = register_pdf(s, library["with_identifiers.pdf"])
        s.commit()
        assert result.outcome is Registration.CREATED
        paper_id = result.paper.id

    assert run_stage(sf, JobKind.PARSE, settings, handle_parse) == 1

    with sf() as s:
        paper = s.get(Paper, paper_id)
        md = s.get(MarkdownDoc, paper_id)
        assert paper.status == PaperStatus.PARSED
        assert md is not None
        assert md.tier == 0, "a clean text layer must not reach the GPU"
        assert md.char_count > 500
        from pathlib import Path

        assert Path(md.md_path).exists()


def test_metadata_stage_extracts_identifiers(sf, settings, library):
    with sf() as s:
        paper_id = register_pdf(s, library["with_identifiers.pdf"]).paper.id
        s.commit()

    run_stage(sf, JobKind.PARSE, settings, handle_parse)
    assert run_stage(sf, JobKind.METADATA, settings, handle_metadata) == 1

    with sf() as s:
        meta = s.get(PaperMeta, paper_id)
        paper = s.get(Paper, paper_id)
        assert meta.doi == "10.1145/3292500.3330701"
        assert meta.arxiv_id == "2401.01234v2"
        assert paper.status == PaperStatus.READY
        # The work key must have been upgraded from the content hash.
        assert paper.work_key == "doi:10.1145/3292500.3330701"


def test_parse_enqueues_metadata(sf, settings, library):
    with sf() as s:
        register_pdf(s, library["with_identifiers.pdf"])
        s.commit()
    run_stage(sf, JobKind.PARSE, settings, handle_parse)

    with sf() as s:
        queued = (
            s.query(Job)
            .filter(Job.kind == JobKind.METADATA, Job.state == JobState.QUEUED)
            .count()
        )
        assert queued == 1


def test_reingesting_the_same_bytes_is_a_noop(sf, settings, library):
    with sf() as s:
        first = register_pdf(s, library["with_identifiers.pdf"])
        s.commit()
    with sf() as s:
        second = register_pdf(s, library["with_identifiers.pdf"])
        s.commit()

    assert second.outcome is Registration.DUPLICATE_CONTENT
    assert second.existing.id == first.paper.id
    with sf() as s:
        assert s.query(Paper).count() == 1


def test_a_moved_file_follows_rather_than_duplicating(sf, settings, library):
    with sf() as s:
        paper_id = register_pdf(s, library["with_identifiers.pdf"]).paper.id
        s.commit()

    moved = settings.library_dir / "renamed.pdf"
    library["with_identifiers.pdf"].rename(moved)

    with sf() as s:
        result = register_pdf(s, moved)
        s.commit()
        assert result.outcome is Registration.MOVED
        assert s.query(Paper).count() == 1
        assert s.get(Paper, paper_id).pdf_path == str(moved.resolve())


def test_quality_report_is_persisted_for_diagnosis(sf, settings, library):
    with sf() as s:
        paper_id = register_pdf(s, library["clean_single_column.pdf"]).paper.id
        s.commit()
    run_stage(sf, JobKind.PARSE, settings, handle_parse)

    with sf() as s:
        report = json.loads(s.get(MarkdownDoc, paper_id).quality_report_json)
        assert report["passed"] is True
        assert report["degraded"] is False
        assert "chars_per_page" in report["metrics"]


def test_a_missing_pdf_fails_the_job_not_the_worker(sf, settings, library):
    with sf() as s:
        register_pdf(s, library["clean_single_column.pdf"])
        s.commit()
    library["clean_single_column.pdf"].unlink()

    with sf() as s:
        job = claim_next(s, kinds=[JobKind.PARSE])
        with pytest.raises(FileNotFoundError):
            handle_parse(s, job, settings)
