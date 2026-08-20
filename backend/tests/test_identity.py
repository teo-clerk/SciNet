"""Paper identity: content hashing and the logical dedup key.

A personal library accumulates the same paper more than once — arXiv v1 then
v2, the preprint then the published version, `paper.pdf` and `paper(1).pdf`.
Byte hashing only catches exact duplicates, so a second, logical key is needed.
"""

from __future__ import annotations

import pytest

from app.services.ingest.hashing import hash_file, is_stable
from app.services.ingest.identity import normalise_doi, work_key

# --- content hash ---------------------------------------------------------


def test_hash_is_stable_for_identical_bytes(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"%PDF-1.7 content")
    b.write_bytes(b"%PDF-1.7 content")
    assert hash_file(a) == hash_file(b)


def test_hash_differs_for_different_bytes(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"one")
    b.write_bytes(b"two")
    assert hash_file(a) != hash_file(b)


def test_hash_handles_large_files_without_loading_them(tmp_path):
    big = tmp_path / "big.pdf"
    big.write_bytes(b"x" * (8 * 1024 * 1024))
    assert len(hash_file(big)) == 64


# --- partial-write detection ----------------------------------------------


def test_is_stable_false_while_a_file_is_still_growing(tmp_path):
    f = tmp_path / "copying.pdf"
    f.write_bytes(b"partial")

    sizes = iter([10, 20])
    assert not is_stable(f, _sizer=lambda _p: next(sizes), _sleep=lambda _s: None)


def test_is_stable_true_when_size_settles(tmp_path):
    f = tmp_path / "done.pdf"
    f.write_bytes(b"complete")
    assert is_stable(f, _sizer=lambda _p: 100, _sleep=lambda _s: None)


def test_is_stable_false_for_empty_file(tmp_path):
    f = tmp_path / "empty.pdf"
    f.touch()
    assert not is_stable(f, _sizer=lambda _p: 0, _sleep=lambda _s: None)


# --- DOI normalisation ----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "10.1145/3292500.3330701",
        "https://doi.org/10.1145/3292500.3330701",
        "http://dx.doi.org/10.1145/3292500.3330701",
        "doi:10.1145/3292500.3330701",
        "DOI: 10.1145/3292500.3330701",
        "10.1145/3292500.3330701.",
        "  10.1145/3292500.3330701  ",
    ],
)
def test_doi_forms_normalise_to_one_value(raw):
    assert normalise_doi(raw) == "10.1145/3292500.3330701"


def test_doi_case_is_normalised():
    assert normalise_doi("10.1145/ABC.DEF") == "10.1145/abc.def"


def test_invalid_doi_returns_none():
    for bad in ("", "not a doi", "11.1145/x", None):
        assert normalise_doi(bad) is None


# --- work key -------------------------------------------------------------


def test_doi_wins_over_everything_else():
    key = work_key(doi="10.1145/x", arxiv_id="2401.01234", title="T", authors=["A B"])
    assert key == "doi:10.1145/x"


def test_arxiv_version_is_stripped_so_v1_and_v2_collide():
    """The single most common duplicate in an arXiv-heavy library."""
    v1 = work_key(arxiv_id="2401.01234v1", title="T", authors=["A B"])
    v2 = work_key(arxiv_id="2401.01234v2", title="T", authors=["A B"])
    assert v1 == v2 == "arxiv:2401.01234"


def test_falls_back_to_title_and_first_author():
    key = work_key(title="Attention Is All You Need", authors=["Ashish Vaswani"])
    assert key == "title:attention-is-all-you-need|vaswani"


def test_title_fallback_ignores_punctuation_and_case():
    a = work_key(title="Attention Is All You Need!", authors=["Ashish Vaswani"])
    b = work_key(title="  attention is all   you need  ", authors=["A. Vaswani"])
    assert a == b


def test_title_fallback_without_authors():
    assert work_key(title="Some Paper") == "title:some-paper|"


def test_last_resort_uses_the_content_hash():
    """A scanned PDF with no extractable identity must still be unique."""
    key = work_key(content_sha256="abc123" * 10)
    assert key.startswith("sha256:")


def test_different_papers_do_not_collide():
    a = work_key(title="Deep Residual Learning", authors=["Kaiming He"])
    b = work_key(title="Deep Reinforcement Learning", authors=["Volodymyr Mnih"])
    assert a != b
