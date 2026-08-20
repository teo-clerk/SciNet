"""Filesystem safety helpers.

The API exposes an endpoint that hands a path to ``xdg-open``. Any path that
reaches the shell must be proven to live inside the library root first, with
symlinks resolved — see the "path traversal" edge case in the design plan.
"""

from __future__ import annotations

from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a path escapes its permitted root."""


def resolve_within(candidate: Path | str, root: Path) -> Path:
    """Resolve ``candidate`` and assert it lives under ``root``.

    Symlinks are followed before the containment check, so a symlink inside the
    library that points outside of it is rejected rather than followed.
    """
    root_real = Path(root).resolve(strict=False)
    path_real = Path(candidate).resolve(strict=False)

    if not path_real.is_relative_to(root_real):
        raise UnsafePathError(f"path escapes library root: {candidate!r}")
    return path_real


def is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


def markdown_path_for(paper_id: int, markdown_dir: Path) -> Path:
    """Shard markdown output so no single directory holds thousands of files."""
    shard = f"{paper_id // 1000:03d}"
    return markdown_dir / shard / f"{paper_id:06d}.md"
