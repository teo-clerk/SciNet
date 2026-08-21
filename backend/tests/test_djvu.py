"""The DjVu reader, and the BZZ codec underneath it.

The codec is checked against DjVuLibre's own encoder rather than against
itself. A decoder tested only on data its own encoder produced proves nothing
about real files, and there is no encoder here to produce any — which is the
point: these fixtures came out of the reference implementation.
"""

from __future__ import annotations

import random

import pytest

from app.services.parse.djvu import NotADjVu, read_djvu_text
from app.services.parse.djvu_bzz import CorruptStream, decompress
from app.services.parse.text_documents import UnreadableDocument, parse_text_document
from tests.fixtures.documents import (
    BZZ_INCOMPRESSIBLE_SEED,
    BZZ_INCOMPRESSIBLE_SIZE,
    DJVU_PAGE_ONE,
    DJVU_PAGE_TWO_OPENING,
    build_djvu_plain_text,
    bzz_incompressible,
    write_djvu_with_text,
)


def test_bzz_matches_the_reference_encoder_on_incompressible_input():
    # Arrange: bytes with no structure to exploit, so nearly every decoded bit
    # takes the least-probable-symbol path that prose almost never reaches.
    expected = random.Random(BZZ_INCOMPRESSIBLE_SEED).randbytes(BZZ_INCOMPRESSIBLE_SIZE)

    # Act
    decoded = decompress(bzz_incompressible())

    # Assert
    assert decoded == expected


def test_bzz_rejects_a_truncated_stream():
    payload = bzz_incompressible()

    with pytest.raises(CorruptStream):
        decompress(payload[: len(payload) // 2])


def test_reads_a_compressed_text_layer(tmp_path):
    path = write_djvu_with_text(tmp_path / "book.djvu")

    text, pages = read_djvu_text(path)

    for phrase in DJVU_PAGE_ONE:
        assert phrase in text
    assert DJVU_PAGE_TWO_OPENING in text
    assert pages == 2


def test_pages_stay_in_order(tmp_path):
    path = write_djvu_with_text(tmp_path / "book.djvu")

    text, _ = read_djvu_text(path)

    assert text.index(DJVU_PAGE_ONE[0]) < text.index(DJVU_PAGE_TWO_OPENING)


def test_reads_an_uncompressed_text_layer(tmp_path):
    # TXTa is legal and some tooling emits it; the chunk walk must not assume
    # every text layer went through the codec.
    path = build_djvu_plain_text(tmp_path / "plain.djvu", "A page of plain text.")

    text, pages = read_djvu_text(path)

    assert text == "A page of plain text."
    assert pages == 1


def test_a_file_without_the_signature_is_not_a_djvu(tmp_path):
    path = tmp_path / "fake.djvu"
    path.write_bytes(b"<html>Access denied</html>")

    with pytest.raises(NotADjVu):
        read_djvu_text(path)


def test_a_scan_with_no_text_layer_fails_loudly(tmp_path):
    # A DjVu that was never OCR'd holds only images. Ingesting it as an empty
    # document would put a blank node on the map; the operator needs to know.
    from app.core.paths import DocumentKind

    path = build_djvu_plain_text(tmp_path / "scan.djvu", "")

    with pytest.raises(UnreadableDocument, match="no text layer"):
        parse_text_document(path, DocumentKind.DJVU)


def test_parses_through_the_pipeline_entry_point(tmp_path):
    from app.core.paths import DocumentKind, classify_document

    path = write_djvu_with_text(tmp_path / "book.djvu")
    assert classify_document(path) is DocumentKind.DJVU

    result = parse_text_document(path, DocumentKind.DJVU)

    assert result.tier == 0
    assert result.parser == "djvu-textlayer"
    assert result.page_count == 2
    assert DJVU_PAGE_ONE[0] in result.markdown


def test_a_damaged_page_does_not_lose_the_whole_book(tmp_path):
    # One corrupt TXTz chunk in a 400-page scan must cost that page, not the
    # other 399.
    path = write_djvu_with_text(tmp_path / "book.djvu")
    raw = bytearray(path.read_bytes())
    marker = raw.find(b"TXTz")
    # Scribble on the second page's compressed payload.
    second = raw.find(b"TXTz", marker + 4)
    raw[second + 12 : second + 40] = b"\xff" * 28
    path.write_bytes(bytes(raw))

    text, _ = read_djvu_text(path)

    assert DJVU_PAGE_ONE[0] in text
    assert DJVU_PAGE_TWO_OPENING not in text
