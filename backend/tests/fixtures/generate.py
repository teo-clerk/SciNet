"""Synthetic PDF fixtures for the parse pipeline.

Generated rather than committed: they are deterministic, tiny, need no network,
and each one isolates exactly one failure mode the quality gate has to catch.
Real papers get added to ``tests/fixtures/real/`` (gitignored) for spot checks.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

PROSE = (
    "We present a method for learning representations of scientific documents "
    "from their citation graph. The model is trained on a large corpus of open "
    "access papers and evaluated on a suite of downstream tasks. Our results "
    "show that the learned embeddings outperform strong baselines on retrieval "
    "and classification, and that the improvement is consistent across fields. "
)

SPANISH = (
    "Presentamos un metodo para aprender representaciones de documentos "
    "cientificos a partir de su grafo de citas. El modelo se entrena con un "
    "corpus amplio de articulos de acceso abierto y se evalua en varias "
    "tareas. Los resultados muestran que las representaciones aprendidas "
    "superan a las lineas base en recuperacion y clasificacion. "
)


def _page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=595, height=842)  # A4


def clean_single_column(path: Path) -> Path:
    """A well-behaved PDF with a good text layer. Must stay on tier 0."""
    doc = pymupdf.open()
    for i in range(3):
        page = _page(doc)
        page.insert_text(
            (72, 80),
            "Learning Scientific Document Embeddings",
            fontsize=16,
            fontname="helv",
        )
        page.insert_textbox(
            pymupdf.Rect(72, 110, 523, 780),
            PROSE * 6,
            fontsize=10,
            fontname="helv",
        )
        page.insert_text((520, 800), str(i + 1), fontsize=9, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def two_column(path: Path) -> Path:
    """Two-column layout — extraction order matters but text is intact."""
    doc = pymupdf.open()
    for _ in range(2):
        page = _page(doc)
        page.insert_textbox(
            pymupdf.Rect(50, 90, 290, 780), PROSE * 4, fontsize=9, fontname="helv"
        )
        page.insert_textbox(
            pymupdf.Rect(305, 90, 545, 780), PROSE * 4, fontsize=9, fontname="helv"
        )
    doc.save(path)
    doc.close()
    return path


def scanned_no_text_layer(path: Path) -> Path:
    """Page is a raster image with no extractable text. Must escalate."""
    src = pymupdf.open()
    page = _page(src)
    page.insert_textbox(
        pymupdf.Rect(72, 110, 523, 700), PROSE * 3, fontsize=11, fontname="helv"
    )
    pix = page.get_pixmap(dpi=110)
    src.close()

    doc = pymupdf.open()
    out = _page(doc)
    out.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
    doc.save(path)
    doc.close()
    return path


def cid_no_whitespace(path: Path) -> Path:
    """Text extracts at normal length but every space is gone.

    This is the classic CID/Type3 silent failure: a length check passes and the
    output is unusable. Must escalate.
    """
    doc = pymupdf.open()
    for _ in range(2):
        page = _page(doc)
        page.insert_textbox(
            pymupdf.Rect(60, 90, 535, 780),
            (PROSE.replace(" ", "") + "\n") * 8,
            fontsize=8,
            fontname="helv",
        )
    doc.save(path)
    doc.close()
    return path


def mojibake(path: Path) -> Path:
    """UTF-8 text decoded as Latin-1 — the most common real encoding failure.

    Extracts at healthy length and is made of ordinary letters, so both the
    volume and whitespace checks pass. Only word-likeness catches it.
    """
    # Produced the way the real thing is, rather than typed out by hand.
    accented = (
        "L'étude présente une méthode pour représenter les documents "
        "scientifiques à partir de leur graphe de citations. Les résultats "
        "montrent une amélioration cohérente. "
    )
    garbled = accented.encode("utf-8").decode("latin-1")

    doc = pymupdf.open()
    for _ in range(2):
        page = _page(doc)
        page.insert_textbox(
            pymupdf.Rect(60, 90, 535, 780),
            garbled * 12,
            fontsize=9,
            fontname="helv",
        )
    doc.save(path)
    doc.close()
    return path


def unmapped_glyphs(path: Path) -> Path:
    """Every glyph extracts as '?' — a font with no usable ToUnicode map."""
    doc = pymupdf.open()
    page = _page(doc)
    page.insert_textbox(
        pymupdf.Rect(60, 90, 535, 780),
        ("??? ?? ???? " * 60),
        fontsize=10,
        fontname="helv",
    )
    doc.save(path)
    doc.close()
    return path


def near_empty(path: Path) -> Path:
    """A cover page with almost nothing on it. Must escalate on volume."""
    doc = pymupdf.open()
    page = _page(doc)
    page.insert_text((72, 400), "Title Page", fontsize=14, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def non_english(path: Path) -> Path:
    """Valid prose in another language. Must NOT escalate."""
    doc = pymupdf.open()
    for _ in range(2):
        page = _page(doc)
        page.insert_textbox(
            pymupdf.Rect(72, 90, 523, 780), SPANISH * 6, fontsize=10, fontname="helv"
        )
    doc.save(path)
    doc.close()
    return path


def with_identifiers(path: Path) -> Path:
    """Carries a DOI and an arXiv ID for the metadata extractor."""
    doc = pymupdf.open()
    page = _page(doc)
    page.insert_text(
        (72, 70),
        "Deep Sets for Molecular Property Prediction",
        fontsize=15,
        fontname="helv",
    )
    page.insert_text(
        (72, 95),
        "Ada Lovelace, Alan Turing, Grace Hopper",
        fontsize=10,
        fontname="helv",
    )
    page.insert_text(
        (72, 120),
        "arXiv:2401.01234v2  [cs.LG]  15 Jan 2024",
        fontsize=9,
        fontname="helv",
    )
    page.insert_text(
        (72, 140),
        "https://doi.org/10.1145/3292500.3330701",
        fontsize=9,
        fontname="helv",
    )
    page.insert_text((72, 175), "Abstract", fontsize=11, fontname="helv")
    page.insert_textbox(
        pymupdf.Rect(72, 190, 523, 400), PROSE * 2, fontsize=10, fontname="helv"
    )
    page.insert_text((72, 430), "1  Introduction", fontsize=11, fontname="helv")
    page.insert_textbox(
        pymupdf.Rect(72, 445, 523, 780), PROSE * 3, fontsize=10, fontname="helv"
    )
    doc.metadata.update({"title": "Deep Sets for Molecular Property Prediction"})
    doc.set_metadata(doc.metadata)
    doc.save(path)
    doc.close()
    return path


BUILDERS = {
    "clean_single_column.pdf": clean_single_column,
    "two_column.pdf": two_column,
    "scanned_no_text_layer.pdf": scanned_no_text_layer,
    "cid_no_whitespace.pdf": cid_no_whitespace,
    "mojibake.pdf": mojibake,
    "unmapped_glyphs.pdf": unmapped_glyphs,
    "near_empty.pdf": near_empty,
    "non_english.pdf": non_english,
    "with_identifiers.pdf": with_identifiers,
}


def build_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {name: fn(out_dir / name) for name, fn in BUILDERS.items()}


if __name__ == "__main__":
    made = build_all(Path(__file__).parent / "generated")
    for name, path in made.items():
        print(f"{name:32s} {path.stat().st_size:>8,} bytes")
