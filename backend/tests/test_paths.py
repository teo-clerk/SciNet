"""The library-root containment guard.

This is what stands between a paper ID and a shell invocation of ``xdg-open``,
so it is tested before anything calls it.
"""

from __future__ import annotations

import pytest

from app.core.paths import UnsafePathError, is_pdf, markdown_path_for, resolve_within


def test_accepts_path_inside_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    target = root / "paper.pdf"
    target.touch()

    assert resolve_within(target, root) == target.resolve()


def test_accepts_nested_path(tmp_path):
    root = tmp_path / "library"
    nested = root / "2024" / "neurips"
    nested.mkdir(parents=True)
    target = nested / "paper.pdf"
    target.touch()

    assert resolve_within(target, root) == target.resolve()


def test_rejects_parent_traversal(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    (tmp_path / "secret.pdf").touch()

    with pytest.raises(UnsafePathError):
        resolve_within(root / ".." / "secret.pdf", root)


def test_rejects_absolute_path_outside_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()

    with pytest.raises(UnsafePathError):
        resolve_within("/etc/passwd", root)


def test_rejects_symlink_escaping_root(tmp_path):
    """A symlink *inside* the library pointing out of it must not be followed."""
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.touch()

    link = root / "innocent.pdf"
    link.symlink_to(outside)

    with pytest.raises(UnsafePathError):
        resolve_within(link, root)


def test_rejects_sibling_prefix_collision(tmp_path):
    """`/data/library-backup` must not pass a check against `/data/library`."""
    root = tmp_path / "library"
    root.mkdir()
    sibling = tmp_path / "library-backup"
    sibling.mkdir()
    target = sibling / "paper.pdf"
    target.touch()

    with pytest.raises(UnsafePathError):
        resolve_within(target, root)


def test_nonexistent_path_inside_root_is_allowed(tmp_path):
    """Containment is a path property; existence is the caller's concern."""
    root = tmp_path / "library"
    root.mkdir()

    assert (
        resolve_within(root / "not-yet.pdf", root) == (root / "not-yet.pdf").resolve()
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.pdf", True), ("a.PDF", True), ("a.Pdf", True), ("a.txt", False), ("a", False)],
)
def test_is_pdf(tmp_path, name, expected):
    assert is_pdf(tmp_path / name) is expected


def test_markdown_path_shards_by_thousand(tmp_path):
    assert markdown_path_for(7, tmp_path) == tmp_path / "000" / "000007.md"
    assert markdown_path_for(1234, tmp_path) == tmp_path / "001" / "001234.md"
    assert markdown_path_for(999_999, tmp_path) == tmp_path / "999" / "999999.md"
