"""Taking non-papers out of the library.

The detectors are heuristics operating on the operator's own files, so the
tests that matter most are the ones asserting what is *not* touched.
"""

from __future__ import annotations

import json

from app.core.paths import PDF_MAGIC
from app.services.ingest.cleanup import (
    MIN_CONTAINER_BYTES,
    QUARANTINE_DIRNAME,
    Reason,
    clean_library,
    inspect_library,
)

PAPER = PDF_MAGIC + b"1.7\n" + b"x" * 4096


def write(root, name: str, data: bytes):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_a_real_paper_is_left_alone(tmp_path):
    write(tmp_path, "good.pdf", PAPER)
    assert inspect_library(tmp_path) == []


def test_html_paywall_saved_as_pdf_is_caught(tmp_path):
    """The exact failure that put two 63,894-byte files in the real library."""
    write(
        tmp_path, "paywall.pdf", b"<!DOCTYPE html><html>Subscribe</html>" + b" " * 4096
    )

    (finding,) = inspect_library(tmp_path)
    assert finding.reason is Reason.NOT_A_DOCUMENT


def test_zero_byte_and_truncated_files_are_caught(tmp_path):
    write(tmp_path, "empty.pdf", b"")
    write(tmp_path, "stub.pdf", b"%PDF-1.4")

    reasons = {f.path.name: f.reason for f in inspect_library(tmp_path)}
    assert reasons == {"empty.pdf": Reason.EMPTY, "stub.pdf": Reason.EMPTY}


def test_a_short_text_note_is_not_mistaken_for_a_truncated_file(tmp_path):
    """The size floor is about container structure, not about content length.

    Caught in a sandbox run: a 540-byte set of reading notes was quarantined as
    "empty" because the PDF threshold was applied to plain text.
    """
    write(tmp_path, "notes.txt", b"A short but perfectly real reading note.")
    write(tmp_path, "idea.md", b"# One idea\n\nWorth keeping.")

    assert inspect_library(tmp_path) == []


def test_whitespace_only_text_is_still_caught(tmp_path):
    write(tmp_path, "blank.md", b"   \n\n\t\n")

    (finding,) = inspect_library(tmp_path)
    assert finding.reason is Reason.EMPTY


def test_files_that_do_not_claim_to_be_papers_are_untouched(tmp_path):
    """A cover image or a bibliography is not broken, just not a paper."""
    write(tmp_path, "cover.png", b"\x89PNG\r\n\x1a\n" + b"0" * 4096)
    write(tmp_path, "refs.bib", b"@article{x}" + b" " * 4096)
    assert inspect_library(tmp_path) == []


def test_byte_identical_duplicates_keep_exactly_one(tmp_path):
    write(tmp_path, "paper.pdf", PAPER)
    write(tmp_path, "downloads/paper (1).pdf", PAPER)
    write(tmp_path, "downloads/deep/copy.pdf", PAPER)

    findings = inspect_library(tmp_path)
    assert len(findings) == 2
    assert all(f.reason is Reason.DUPLICATE for f in findings)
    # The shallowest copy survives.
    assert {f.path.name for f in findings} == {"paper (1).pdf", "copy.pdf"}
    assert all(f.duplicate_of.name == "paper.pdf" for f in findings)


def test_a_registered_copy_is_kept_over_an_unregistered_one(tmp_path):
    """Removing the file a Paper row points at would break the row."""
    shallow = write(tmp_path, "copy.pdf", PAPER)
    registered = write(tmp_path, "sorted/2024/original.pdf", PAPER)

    (finding,) = inspect_library(tmp_path, keep_paths={str(registered.resolve())})
    assert finding.path == shallow
    assert finding.duplicate_of == registered


def test_near_duplicates_are_not_touched(tmp_path):
    """Only byte-identical files. Two versions of a paper are two papers."""
    write(tmp_path, "v1.pdf", PAPER)
    write(tmp_path, "v2.pdf", PAPER + b"revised")
    assert inspect_library(tmp_path) == []


def test_clean_moves_to_quarantine_and_records_why(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    write(library, "good.pdf", PAPER)
    write(library, "junk.pdf", b"<html>" + b" " * 4096)

    findings, destination = clean_library(library)

    assert len(findings) == 1
    assert not (library / "junk.pdf").exists()
    assert (library / "good.pdf").exists()

    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["files"][0]["reason"] == Reason.NOT_A_DOCUMENT.value
    assert destination.parent.name == QUARANTINE_DIRNAME


def test_purge_deletes_outright(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    junk = write(library, "junk.pdf", b"<html>" + b" " * 4096)

    findings, destination = clean_library(library, purge=True)

    assert len(findings) == 1
    assert not junk.exists()
    assert destination is None


def test_dry_run_changes_nothing(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    junk = write(library, "junk.pdf", b"<html>" + b" " * 4096)

    findings, destination = clean_library(library, dry_run=True)

    assert len(findings) == 1
    assert junk.exists()
    assert destination is None


def test_quarantined_files_are_not_re_examined(tmp_path):
    """Otherwise every run would quarantine the previous run's quarantine."""
    library = tmp_path / "library"
    library.mkdir()
    write(library, "junk.pdf", b"<html>" + b" " * 4096)

    clean_library(library)
    findings, _ = clean_library(library)
    assert findings == []


def test_same_name_from_different_folders_does_not_collide(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    write(library, "a/junk.pdf", b"<html>" + b" " * 4096)
    write(library, "b/junk.pdf", b"<html>" + b" " * 5000)

    findings, destination = clean_library(library)
    assert len(findings) == 2
    quarantined = list(destination.rglob("*.pdf"))
    assert len(quarantined) == 2


def test_minimum_size_is_far_below_a_real_paper(tmp_path):
    """Guards the threshold against being raised into real-paper territory."""
    assert MIN_CONTAINER_BYTES < 14_000
