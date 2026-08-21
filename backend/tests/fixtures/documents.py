"""Fixtures for every library format that is not a PDF.

Built in code rather than committed as binaries, like the PDF fixtures next
door — a synthetic EPUB whose headings are known is far more useful in a test
than an opaque file, and it cannot rot.

The two exceptions are base64 blobs, and they are deliberate. Both are output
from DjVuLibre's own encoder, kept verbatim because the point of the DjVu tests
is to prove that this project's pure-Python decoder agrees with the reference
implementation. Generating them here would mean writing a BZZ *encoder* to test
the decoder against, which proves only that the two agree with each other.
"""

from __future__ import annotations

import base64
import struct
import zipfile
from pathlib import Path

#: A two-page bundled DjVu carrying a BZZ-compressed OCR text layer, produced
#: by ``djvused ... set-txt`` and ``djvm -c`` (DjVuLibre 3.5.30).
DJVU_WITH_TEXT_B64 = (
    "QVQmVEZPUk0AAAIfREpWTURJUk0AAAAigQACAAAAOgAAATL//+i/ih/s0rUhb2QVc2JR"
    "2nV2Q4MJJUZPUk0AAADvREpWVUlORk8AAAAKAEAAQBgALAEWAVNqYnoAAAADgfvkAFRY"
    "VHoAAADF//7UiSo4L1RQEPodFkI7DdyPTYab86O51DAVmyGo87aj+2Dh3X4odlu9E/uS"
    "t3J0c7O+E+TS/IJrly7EkQoSUZ+F3HEts8Iyla9JLWh7XkJsWaQxS162eOYJGvYfHYJB"
    "TXTIV3Oh6OLf3dPLQfkJXAGcpRASZub0m3hkfLo3jwxJ64dLUZAo0fz2crYycVVRzGzq"
    "CCcYj2+wA+lQWzg6FPfm9cVPj3BiIT3HXI5pnZ8mneQn7lOfVoGF6AbVW/rZxPV184cA"
    "Rk9STQAAAPFESlZVSU5GTwAAAAoAQABAGAAsARYBU2piegAAAAOB++QAVFhUegAAAMf/"
    "/tuJNnVW77YRPhhuR5re76/V3aNTo2dAKZWV2potwx+fUu6q/FEe13Rv8ugwP9583Dxi"
    "ZDzLEIx9X+9REBIcMJzv+NVgyFmB2hhu3exYmoQ2Md2tsEEiOqN5MEkyA+BBM+3bKN9d"
    "4/pSFYyhFu2U2Gy55Sj+L39x7+lmS9+1QDNE7vAJh+Mcd692j3TzYVRc1jAtvlkHf/NB"
    "V6aQpFb/HUyXjIM1La0uh4sIm/ivioQO2inSswoB6dCOozTx0EJYXL6IDqon"
)

#: ``bzz -e`` of 1,500 bytes from ``random.Random(11).randbytes``. Incompressible
#: input is the case worth keeping: it drives the arithmetic coder down its
#: least-probable-symbol path on nearly every bit, which prose never does.
BZZ_INCOMPRESSIBLE_B64 = (
    "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"
)
BZZ_INCOMPRESSIBLE_SEED = 11
BZZ_INCOMPRESSIBLE_SIZE = 1500

DJVU_PAGE_ONE = (
    "Introduction to Algebraic Topology",
    "Andrew H. Wallace",
)
DJVU_PAGE_TWO_OPENING = "Chapter 1. Intuitive Description"


def write_djvu_with_text(path: Path) -> Path:
    path.write_bytes(base64.b64decode("".join(DJVU_WITH_TEXT_B64)))
    return path


def bzz_incompressible() -> bytes:
    return base64.b64decode("".join(BZZ_INCOMPRESSIBLE_B64))


def build_djvu_plain_text(path: Path, text: str) -> Path:
    """A single-page DjVu whose text layer is stored uncompressed (``TXTa``).

    The uncompressed variant is rare in the wild but legal, and it is the only
    part of the container this project can also *write*, which makes it the
    right way to test the chunk walk independently of the codec.
    """
    body = text.encode("utf-8")
    # A TXT chunk is a 24-bit length, the UTF-8 text, then the zone tree. An
    # empty zone tree is what a page with no word boxes looks like.
    payload = len(body).to_bytes(3, "big") + body

    def chunk(name: bytes, data: bytes) -> bytes:
        pad = b"\x00" if len(data) & 1 else b""
        return name + struct.pack(">I", len(data)) + data + pad

    info = chunk(b"INFO", struct.pack(">HHHHBB", 2550, 3300, 24, 300, 22, 0))
    inner = b"DJVU" + info + chunk(b"TXTa", payload)
    path.write_bytes(b"AT&T" + chunk(b"FORM", inner))
    return path


def build_epub(
    path: Path,
    *,
    title: str,
    author: str,
    year: str,
    chapters: list[tuple[str, str]],
) -> Path:
    """A minimal but structurally valid EPUB 3.

    Chapters are listed in the spine in reverse filename order on purpose, so a
    reader that walks the zip alphabetically instead of following the spine
    produces visibly wrong output.
    """
    names = [f"ch{n}.xhtml" for n in range(len(chapters))]
    manifest = "".join(
        f'<item id="c{n}" href="{name}" media-type="application/xhtml+xml"/>'
        for n, name in enumerate(names)
    )
    spine = "".join(f'<itemref idref="c{n}"/>' for n in range(len(names)))

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="3.0" unique-identifier="id"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>"
            f"<dc:language>en</dc:language><dc:date>{year}</dc:date>"
            f"</metadata><manifest>{manifest}</manifest><spine>{spine}</spine>"
            "</package>",
        )
        for name, (heading, body) in zip(names, chapters, strict=True):
            archive.writestr(
                f"OEBPS/{name}",
                '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
                f"<head><title>{heading}</title></head><body><h1>{heading}</h1>"
                f"<p>{body}</p></body></html>",
            )
    return path


def build_mobi(path: Path, *, title: str, html: str) -> Path:
    """A minimal uncompressed MOBI (PalmDB, ``BOOKMOBI``).

    Compression is set to "none" so this stays readable without a PalmDOC
    encoder; MuPDF handles that field, and what is under test here is that the
    pipeline recognises and routes the format, not MuPDF's decompressor.
    """
    text = html.encode("utf-8")
    record_size = 4096
    records = [text[i : i + record_size] for i in range(0, len(text), record_size)]
    title_bytes = title.encode("utf-8")

    palmdoc = struct.pack(">HHIHHHH", 1, 0, len(text), len(records), record_size, 0, 0)
    header_length = 232
    mobi = bytearray(header_length)
    mobi[0:4] = b"MOBI"
    struct.pack_into(">I", mobi, 4, header_length)
    struct.pack_into(">I", mobi, 8, 2)  # book
    struct.pack_into(">I", mobi, 12, 65001)  # utf-8
    struct.pack_into(">I", mobi, 16, 0xFFFFFFFF)
    struct.pack_into(">I", mobi, 20, 6)
    for offset in range(24, 64, 4):
        struct.pack_into(">I", mobi, offset, 0xFFFFFFFF)
    struct.pack_into(">I", mobi, 64, 1)
    struct.pack_into(">I", mobi, 68, 16 + header_length)  # full name offset
    struct.pack_into(">I", mobi, 72, len(title_bytes))
    struct.pack_into(">I", mobi, 76, 9)  # locale: en
    struct.pack_into(">I", mobi, 92, len(records) + 1)
    struct.pack_into(">I", mobi, 112, 0)  # no EXTH

    blocks = [palmdoc + bytes(mobi) + title_bytes + b"\x00\x00", *records]
    prologue = 78 + len(blocks) * 8 + 2
    offsets, cursor = [], prologue
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)

    out = bytearray()
    out += title_bytes[:31].replace(b" ", b"_").ljust(32, b"\x00")
    out += struct.pack(">HHIIIIII", 0, 0, 0, 0, 0, 0, 0, 0)
    out += b"BOOK" + b"MOBI"
    out += struct.pack(">IIH", 0, 0, len(blocks))
    for index, offset in enumerate(offsets):
        out += struct.pack(">IBBBB", offset, 0, 0, 0, index)
    out += b"\x00\x00"
    for block in blocks:
        out += block
    path.write_bytes(bytes(out))
    return path
