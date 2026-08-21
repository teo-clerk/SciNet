"""Turning XHTML into the Markdown the rest of the pipeline reads.

EPUB chapters are XHTML, and their headings are *semantic* — an ``<h2>`` says
"this is a section title" because the author's tooling said so. That is far
better evidence than the font-size guessing a PDF converter has to fall back
on, and the metadata stage depends on it: titles and chapter structure are both
found by looking for Markdown headings.

Deliberately not a general HTML-to-Markdown converter. Inline emphasis, links
and images are all dropped, because none of them survive into an embedding as
anything but noise. What is kept is the block structure: headings, paragraphs,
and list items.
"""

from __future__ import annotations

from html.parser import HTMLParser

#: Elements whose contents are code or presentation, never prose.
_SKIPPED = frozenset({"script", "style", "head", "svg", "math", "noscript"})
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
#: Elements that end the current block of text.
_BLOCKS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "blockquote",
        "pre",
        "tr",
        "td",
        "th",
        "figcaption",
        "dd",
        "dt",
        "table",
        "ul",
        "ol",
        "body",
        "aside",
        "header",
        "footer",
        "nav",
        "main",
    }
)


class _MarkdownExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buffer: list[str] = []
        self._skip_depth = 0
        self._heading: int | None = None
        self._list_item = False

    # --- block handling -------------------------------------------------

    def _flush(self) -> None:
        text = " ".join("".join(self._buffer).split())
        self._buffer.clear()
        if not text:
            self._heading = None
            self._list_item = False
            return
        if self._heading is not None:
            self.blocks.append(f"{'#' * self._heading} {text}")
        elif self._list_item:
            self.blocks.append(f"- {text}")
        else:
            self.blocks.append(text)
        self._heading = None
        self._list_item = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if self._skip_depth:
            self._skip_depth += 1 if tag in _SKIPPED else 0
            return
        if tag in _SKIPPED:
            self._skip_depth = 1
            return
        if tag == "br":
            self._flush()
            return
        if tag in _HEADINGS:
            self._flush()
            self._heading = _HEADINGS[tag]
            return
        if tag == "li":
            self._flush()
            self._list_item = True
            return
        if tag in _BLOCKS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        if tag == "br" and not self._skip_depth:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in _SKIPPED:
                self._skip_depth -= 1
            return
        if tag in _HEADINGS or tag == "li" or tag in _BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buffer.append(data)

    def close(self) -> None:  # noqa: D102 - inherited contract
        super().close()
        self._flush()


def html_to_markdown(html: str) -> str:
    """Block structure only: headings, paragraphs and list items."""
    parser = _MarkdownExtractor()
    # Malformed markup is the norm in real EPUBs. HTMLParser is lenient by
    # design and recovers in place, so the only failure worth guarding is one
    # that stops it outright — in which case whatever was parsed still stands.
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - partial text beats no text
        parser._flush()
    return "\n\n".join(parser.blocks)
