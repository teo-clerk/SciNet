"""Taking a file the pipeline cannot read out of the library.

Distinct from ``cleanup``, which inspects the whole library before ingestion
and removes what is obviously not a paper. This runs afterwards, on the
evidence of a parse that actually failed: an encrypted book, a PDF whose
container will not open, a DjVu that was never OCR'd. Those look like perfectly
ordinary documents from the outside — there is nothing about the bytes that
says so until something tries to read them.

Leaving them in place is what made this worth building. The watcher re-notices
them, a rescan re-registers them, and every run spends its attempts on the same
handful of files before reaching anything new; meanwhile the operator sees a
paper count short of the file count with nothing saying which files or why.

**Moved, never deleted.** A file is here because one parser gave up on it,
which is a much weaker claim than the content checks in ``cleanup`` make, and
the diagnosis is recorded next to it so the decision can be reviewed and
undone. ``restore`` puts one back.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.core.paths import UnsafePathError, resolve_within
from app.core.types import utcnow
from app.services.ingest.cleanup import MANIFEST_NAME, QUARANTINE_DIRNAME, _unique

logger = logging.getLogger(__name__)

#: Failures are filed by day rather than by run. A batch import produces a
#: steady trickle of them over hours, and one directory per failure would be
#: unreadable while one directory for all time would never be safe to delete.
FOLDER_FORMAT = "%Y%m%d"


@dataclass(frozen=True)
class Quarantined:
    original: Path
    destination: Path
    reason: str


def quarantine_root(library: Path) -> Path:
    return library.parent / QUARANTINE_DIRNAME


def quarantine_document(path: Path, library: Path, reason: str) -> Quarantined | None:
    """Move one unreadable file out of the library. Returns None if it cannot.

    Never raises. This runs inside the worker's failure handler, which is the
    one place that must not itself fail — a file that will not move is a
    logged annoyance, where an exception here would strand the job that was
    being written off.
    """
    try:
        source = resolve_within(path, library)
    except UnsafePathError:
        # Already outside the library — a paper registered from elsewhere, or
        # one this function moved on an earlier run. Nothing to do.
        logger.debug("%s is not inside the library; not quarantining", path)
        return None

    if not source.exists():
        return None

    destination_dir = quarantine_root(library) / date.today().strftime(FOLDER_FORMAT)
    try:
        relative = source.relative_to(library)
    except ValueError:  # pragma: no cover - resolve_within already proved this
        relative = Path(source.name)

    target = _unique(destination_dir / relative)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    except OSError:
        logger.exception("could not quarantine %s", source)
        return None

    _append_manifest(destination_dir, source, target, reason)
    logger.info("quarantined %s: %s", source.name, reason)
    return Quarantined(source, target, reason)


def _append_manifest(
    directory: Path, original: Path, target: Path, reason: str
) -> None:
    """Record why, alongside the files. Read by nobody; written for a human.

    Rewritten whole rather than appended to, because the file is small, the
    write is rare, and a half-written JSON array is worse than a slow one.
    """
    manifest = directory / MANIFEST_NAME
    entries: list[dict[str, str]] = []
    if manifest.exists():
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            logger.warning("%s is unreadable; starting a new manifest", manifest)

    entries.append(
        {
            "original": str(original),
            "quarantined": str(target),
            "reason": reason,
            "at": utcnow().isoformat(),
        }
    )
    try:
        manifest.write_text(json.dumps({"files": entries}, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("could not write %s", manifest)


def restore(target: Path, library: Path) -> Path | None:
    """Put a quarantined file back, under its original name. None if it cannot.

    The manifest records where it came from, but the file is returned to the
    library root rather than its original subfolder: the folder may be gone,
    and the watcher does not care where under the library a file appears.
    """
    root = quarantine_root(library)
    try:
        source = resolve_within(target, root)
    except UnsafePathError:
        logger.warning("%s is not in quarantine; refusing to move it", target)
        return None
    if not source.exists():
        return None

    destination = _unique(library / source.name)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except OSError:
        logger.exception("could not restore %s", source)
        return None
    return destination
