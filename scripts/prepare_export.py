#!/usr/bin/env python
"""Clean the checkout so it can be zipped up and handed to someone else.

Deletes only what is *regenerated* — bytecode caches, tool caches, build
output. It never touches anything the recipient cannot rebuild: the library,
the database, the extracted Markdown, the vectors and the downloaded models all
stay, because the point of the exercise is a copy that works offline on a
machine that has never seen this project.

It does not make the archive. Two directories have to be left out and only the
person zipping can leave them out — ``.venv`` and ``node_modules`` are platform-
specific, so a Linux virtualenv unpacked on Windows is broken in a way that is
hard to diagnose and trivial to avoid. ``uv sync`` and ``bun install`` rebuild
both from the lockfiles that *are* included. See USAGE.md.

    uv run python ../scripts/prepare_export.py            # report only
    uv run python ../scripts/prepare_export.py --apply    # clean
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Directories that exist only to make the next run faster. Every one of these
#: is rebuilt on demand, and several are actively harmful to ship: bytecode
#: caches carry absolute paths from this machine.
DISPOSABLE_DIRS = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".vite",
    ".turbo",
)

#: Individual files with the same property.
DISPOSABLE_GLOBS = ("*.pyc", "*.pyo", ".DS_Store", "*.egg-info")

#: Never descended into. Excluded from the archive rather than cleaned, so
#: walking them is wasted time on a 5 GB virtualenv.
SKIP_WALK = {".venv", "node_modules", ".git"}

#: Must survive, and the reason. Checked rather than assumed — the whole
#: promise of the export is that these arrive intact.
MUST_SURVIVE = {
    "data/library": "the documents themselves",
    "data/scinet.db": "every paper, cluster, tag and coordinate",
    "data/models": "the models, so it runs offline",
    "data/markdown": "extracted text, so nothing is re-parsed",
    "data/vectors": "the embeddings, so nothing is re-embedded",
}

#: Left out of the archive by the person zipping, not deleted here — the
#: sender still needs them to run the project themselves.
EXCLUDE_FROM_ZIP = {
    ".venv": "uv sync",
    "backend/.venv": "uv sync",
    "frontend/node_modules": "bun install",
    "frontend/dist": "bun run build",
    ".git": "not needed to run it",
}


@dataclass(frozen=True)
class Removable:
    path: Path
    bytes: int


def directory_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def human(size: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def find_disposable() -> list[Removable]:
    found: list[Removable] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_WALK for part in path.relative_to(ROOT).parts):
            continue
        if path.is_dir() and path.name in DISPOSABLE_DIRS:
            found.append(Removable(path, directory_size(path)))
        elif path.is_file() and any(path.match(g) for g in DISPOSABLE_GLOBS):
            try:
                found.append(Removable(path, path.stat().st_size))
            except OSError:
                continue
    # Deepest first, so removing a parent does not invalidate a child entry.
    return sorted(found, key=lambda r: len(r.path.parts), reverse=True)


def check_survivors() -> list[str]:
    """Anything the recipient needs that is not here. Reported, never fixed."""
    return [
        f"{name} is missing — {why}"
        for name, why in sorted(MUST_SURVIVE.items())
        if not (ROOT / name).exists()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="delete (default: report)")
    args = parser.parse_args()

    disposable = find_disposable()
    reclaimed = sum(r.bytes for r in disposable)

    print(f"disposable: {len(disposable)} item(s), {human(reclaimed)}")
    for removable in disposable[:10]:
        print(f"  {removable.path.relative_to(ROOT)}")
    if len(disposable) > 10:
        print(f"  … and {len(disposable) - 10} more")

    if args.apply:
        for removable in disposable:
            try:
                if removable.path.is_dir():
                    shutil.rmtree(removable.path)
                else:
                    removable.path.unlink()
            except OSError as error:
                print(f"  could not remove {removable.path}: {error}", file=sys.stderr)
        print(f"\nremoved, reclaiming {human(reclaimed)}")

    print("\nkept, because the recipient cannot rebuild it:")
    payload = 0
    for name, why in sorted(MUST_SURVIVE.items()):
        target = ROOT / name
        if not target.exists():
            continue
        size = target.stat().st_size if target.is_file() else directory_size(target)
        payload += size
        print(f"  {human(size):>10}  {name:<18} {why}")

    print("\nleave out of the archive — platform-specific, and rebuildable:")
    excluded = 0
    for name, how in sorted(EXCLUDE_FROM_ZIP.items()):
        target = ROOT / name
        if not target.exists():
            continue
        size = directory_size(target)
        excluded += size
        print(f"  {human(size):>10}  {name:<22} {how}")

    print(f"\narchive would be roughly {human(payload)} before compression.")
    if payload > 8 * 1024**3:
        # Said plainly rather than left for them to discover after an hour of
        # zipping: this is past what most transfer services accept, and the
        # models are nearly all of it.
        print(
            "  That is too large for email and for most upload limits. Nearly\n"
            "  all of it is data/models. Leaving those out takes the archive to\n"
            f"  about {human(payload - directory_size(ROOT / 'data' / 'models'))};\n"
            "  the recipient then runs scripts/download_models.py once, online."
        )

    for problem in check_survivors():
        print(f"\nWARNING: {problem}", file=sys.stderr)

    if not args.apply:
        print("\nreport only — pass --apply to delete the disposable items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
