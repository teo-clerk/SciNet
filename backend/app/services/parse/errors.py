"""Telling a document that cannot be read from a machine that is having a bad day.

The queue retries a failed job three times, which is right for a model server
that was restarting and wrong for a DRM-locked book: the second and third
attempts re-render the same pages, re-download the same weights and reach the
same conclusion, and the only thing they add is delay in front of everything
else in the queue. A failing job is re-claimed ahead of the documents behind it
— ``fail()`` requeues and ``claim_next`` orders by id — so three attempts at an
unreadable file is three times the wait for its neighbour, not just for itself.

The distinction is whether the *document* is the problem or the *environment*
is. Encryption, a missing text layer, a corrupt container: those are properties
of the file and no amount of retrying changes them. A timed-out inference call,
an unreachable Ollama, a locked database: those are properties of the moment.

Errors this project raises say which they are by inheriting from
``FatalDocument``. Errors raised by libraries we do not own are matched by type
below, deliberately as a short and conservative list — the cost of getting this
wrong in the fatal direction is a readable document quarantined on its first
bad minute, which is worse than a slow retry.
"""

from __future__ import annotations


class FatalDocument(Exception):
    """This file cannot be read, and will not be readable on a retry.

    Raised where the document itself is the obstacle. Anything inheriting from
    this fails its job immediately rather than burning its remaining attempts.
    """


def _external_fatal_types() -> tuple[type[BaseException], ...]:
    """Third-party exceptions that mean "this file is not readable".

    Imported lazily and defensively: PyMuPDF's error classes have moved
    between releases, and a portability check that dies because a name was
    renamed would be worse than a retry.
    """
    types: list[type[BaseException]] = [FileNotFoundError, IsADirectoryError]
    try:
        import pymupdf

        for name in ("FileDataError", "EmptyFileError"):
            candidate = getattr(pymupdf, name, None)
            if isinstance(candidate, type) and issubclass(candidate, BaseException):
                types.append(candidate)
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        pass
    return tuple(types)


def is_fatal(exc: BaseException) -> bool:
    """Should this failure skip the remaining retries?

    Conservative by construction: anything not recognised is treated as
    transient and retried, because retrying a broken file wastes minutes while
    giving up on a good one loses it from the library until somebody notices.
    """
    if isinstance(exc, FatalDocument):
        return True
    return isinstance(exc, _external_fatal_types())
