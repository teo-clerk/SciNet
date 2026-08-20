"""Content hashing and partial-write detection for the watched library."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
STABILITY_INTERVAL_SECONDS = 1.0


def hash_file(path: Path | str) -> str:
    """SHA-256 of the file, read in chunks.

    Streamed rather than slurped: a scanned paper can be hundreds of megabytes
    and the worker already competes for RAM with the model stack.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def is_stable(
    path: Path | str,
    *,
    interval: float = STABILITY_INTERVAL_SECONDS,
    _sizer: Callable[[Path], int] | None = None,
    _sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once the file has stopped growing.

    A PDF being copied into the library fires modify events continuously, and
    hashing it mid-copy writes a hash that will never be seen again — poisoning
    dedup for that paper permanently. Waiting for the size to settle is the
    portable version of watching for IN_CLOSE_WRITE.
    """
    p = Path(path)
    size_of = _sizer or (lambda q: q.stat().st_size)

    first = size_of(p)
    if first <= 0:
        return False

    _sleep(interval)
    return size_of(p) == first
