"""Removing what should never have been in the library.

A real library accumulates two kinds of file that are not papers and never
will be: things that claim to be documents but are not — an HTML paywall
interstitial saved as ``.pdf``, a truncated download, a zero-byte placeholder —
and exact byte-identical copies of papers already present.

Skipping them at ingestion was not enough. They stayed on disk, were rescanned
on every startup, and had to be re-diagnosed by hand each time the operator
wondered why the paper count was short. So they are taken out of the library.

**Nothing is deleted by default.** Files are moved to a timestamped quarantine
directory with a manifest recording why, because the detectors are heuristics
and a false positive against a real paper is unrecoverable. ``purge=True``
deletes outright for operators who want the literal behaviour.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.core.paths import (
    SUPPORTED_EXTENSIONS,
    DocumentKind,
    classify_document,
)
from app.core.types import utcnow
from app.services.ingest.hashing import hash_file

logger = logging.getLogger(__name__)

QUARANTINE_DIRNAME = "quarantine"
MANIFEST_NAME = "manifest.json"

# Below this, a *container* format is truncated: a PDF or .docx carries
# mandatory structure — headers, an xref table, a zip directory — that cannot
# fit in less. The smallest real PDF in the reference corpus is ~14 kB, so this
# is set far below anything genuine.
#
# It deliberately does not apply to .txt and .md. Those have no structural
# floor, and a short reading note is a legitimate document: an early version of
# this rule quarantined a 540-byte set of notes as "empty".
MIN_CONTAINER_BYTES = 512
CONTAINER_KINDS = {DocumentKind.PDF, DocumentKind.DOCX}


class Reason(StrEnum):
    EMPTY = "empty"
    NOT_A_DOCUMENT = "not_a_document"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class Finding:
    path: Path
    reason: Reason
    detail: str
    #: For duplicates, the copy that is being kept.
    duplicate_of: Path | None = None


def _claims_to_be_a_document(path: Path) -> bool:
    """Is this file presenting itself as a paper?

    Only files that make the claim are candidates for removal. A ``.png`` cover
    or a ``.bib`` file sitting in the library is not broken — it is simply not a
    paper, and deleting things the operator deliberately filed there would be a
    far worse failure than leaving them alone. Extensionless files count,
    because that is how arXiv downloads arrive.
    """
    suffix = path.suffix.lower()
    return suffix in SUPPORTED_EXTENSIONS or suffix == ""


def inspect_library(root: Path, *, keep_paths: set[str] | None = None) -> list[Finding]:
    """Find everything in ``root`` that should not be there.

    ``keep_paths`` names files already registered as papers. When a set of
    byte-identical copies is found, a registered one is kept in preference to
    an unregistered one — removing the file a Paper row points at would break
    the row to tidy up its duplicate.
    """
    keep_paths = keep_paths or set()
    findings: list[Finding] = []
    by_digest: dict[str, list[Path]] = {}

    for path in sorted(root.rglob("*")):
        if not path.is_file() or _in_quarantine(path, root):
            continue

        if not _claims_to_be_a_document(path):
            continue

        size = path.stat().st_size
        if size == 0:
            findings.append(Finding(path, Reason.EMPTY, "zero bytes"))
            continue

        kind = classify_document(path)
        if kind is None:
            findings.append(
                Finding(
                    path,
                    Reason.NOT_A_DOCUMENT,
                    f"{path.suffix or 'no extension'} but the contents are not a "
                    "PDF, Word document, or text",
                )
            )
            continue

        if kind in CONTAINER_KINDS and size < MIN_CONTAINER_BYTES:
            findings.append(
                Finding(path, Reason.EMPTY, f"{size} bytes is a truncated {kind.value}")
            )
            continue

        if kind not in CONTAINER_KINDS and not path.read_bytes().strip():
            findings.append(Finding(path, Reason.EMPTY, "contains only whitespace"))
            continue

        by_digest.setdefault(hash_file(path), []).append(path)

    for digest, copies in by_digest.items():
        if len(copies) < 2:
            continue
        keeper = _pick_keeper(copies, keep_paths)
        for copy in copies:
            if copy == keeper:
                continue
            findings.append(
                Finding(
                    copy,
                    Reason.DUPLICATE,
                    f"byte-identical to {keeper.name} ({digest[:12]})",
                    duplicate_of=keeper,
                )
            )

    return findings


def _pick_keeper(copies: list[Path], keep_paths: set[str]) -> Path:
    """Which copy survives: a registered one, else the shallowest path."""
    registered = [c for c in copies if str(c.resolve()) in keep_paths]
    pool = registered or copies
    # Shallowest, then alphabetical — deterministic, and prefers the copy in
    # the library root over one buried in a "downloads" subfolder.
    return min(pool, key=lambda c: (len(c.parts), str(c)))


def _in_quarantine(path: Path, root: Path) -> bool:
    quarantine = root.parent / QUARANTINE_DIRNAME
    return path.is_relative_to(quarantine) if quarantine.exists() else False


def clean_library(
    root: Path,
    *,
    keep_paths: set[str] | None = None,
    purge: bool = False,
    dry_run: bool = False,
) -> tuple[list[Finding], Path | None]:
    """Take the findings out of the library.

    Returns the findings and the quarantine directory, if one was made.
    """
    findings = inspect_library(root, keep_paths=keep_paths)
    if not findings or dry_run:
        return findings, None

    if purge:
        for finding in findings:
            finding.path.unlink(missing_ok=True)
            logger.info("purged %s (%s)", finding.path, finding.reason.value)
        return findings, None

    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    destination = root.parent / QUARANTINE_DIRNAME / stamp
    destination.mkdir(parents=True, exist_ok=True)

    moved: list[dict[str, str]] = []
    for finding in findings:
        try:
            relative = finding.path.relative_to(root)
        except ValueError:
            relative = Path(finding.path.name)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Two files with the same name from different subfolders would collide.
        target = _unique(target)
        shutil.move(str(finding.path), str(target))
        moved.append(
            {
                "original": str(finding.path),
                "quarantined": str(target),
                "reason": finding.reason.value,
                "detail": finding.detail,
            }
        )
        logger.info("quarantined %s (%s)", finding.path, finding.reason.value)

    (destination / MANIFEST_NAME).write_text(
        json.dumps({"quarantined_at": stamp, "files": moved}, indent=2),
        encoding="utf-8",
    )
    return findings, destination


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    for n in range(1, 1000):
        candidate = target.with_name(f"{target.stem}~{n}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot find a free name for {target}")
