"""Loading the embedding model without making anyone wait for it.

Loading Qwen3-Embedding into CPU memory takes about 25 seconds. Doing it
lazily makes the first person to search pay all of it; doing it during startup
makes everyone wait for a map that does not need the model at all. Neither is
necessary — the load is slow but it is not urgent, so it happens on a
background thread while the API serves everything else.

A thread rather than an asyncio task, deliberately: ``SentenceTransformer(...)``
is blocking CPU-bound work, and scheduling it on the event loop would stall the
very requests this exists to keep responsive.

Callers that need the model ask ``state()``. Until it reports ``ready`` they
are told the engine is still warming, which is a different answer from "no
results" and the interface should say so.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class WarmupState(StrEnum):
    COLD = "cold"  # not started
    WARMING = "warming"  # loading now; ask again shortly
    READY = "ready"  # usable
    FAILED = "failed"  # will not become ready without intervention


@dataclass(frozen=True)
class WarmupStatus:
    state: WarmupState
    elapsed_seconds: float
    error: str | None = None
    #: Rough seconds until ready, for a caller deciding when to retry. A
    #: measured estimate rather than a promise; it is only used to pace polling.
    estimated_remaining: float | None = None


#: Observed cold-load time on this hardware. Only used to shape a retry hint.
EXPECTED_LOAD_SECONDS = 26.0


class ModelWarmer:
    """Tracks a single background load, and never starts a second one."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = WarmupState.COLD
        self._error: str | None = None
        self._started_at: float | None = None
        self._finished_at: float | None = None
        self._thread: threading.Thread | None = None

    def status(self) -> WarmupStatus:
        with self._lock:
            if self._started_at is None:
                return WarmupStatus(self._state, 0.0, self._error)
            end = self._finished_at or time.monotonic()
            elapsed = end - self._started_at
            remaining = (
                max(0.0, EXPECTED_LOAD_SECONDS - elapsed)
                if self._state is WarmupState.WARMING
                else None
            )
            return WarmupStatus(self._state, elapsed, self._error, remaining)

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._state is WarmupState.READY

    def start(self) -> None:
        """Begin loading, unless a load already started. Returns immediately."""
        with self._lock:
            if self._state in (WarmupState.WARMING, WarmupState.READY):
                return
            self._state = WarmupState.WARMING
            self._error = None
            self._started_at = time.monotonic()
            self._finished_at = None
            # Daemon: a half-finished warmup must never keep the process alive
            # when the operator asks it to stop.
            self._thread = threading.Thread(
                target=self._load, name="embed-warmup", daemon=True
            )
            self._thread.start()

    def _load(self) -> None:
        try:
            from app.core.config import get_settings
            from app.services.embed import encoder

            settings = get_settings()
            logger.info(
                "warming %s on %s in the background",
                settings.embed_model,
                encoder.QUERY_DEVICE,
            )
            # Encoding one short string forces the tokenizer and the weights to
            # load, so the first real query finds everything in place. Loading
            # the model object alone leaves lazy work for that query to pay.
            encoder.encode_query("warmup", settings=settings)

            with self._lock:
                self._state = WarmupState.READY
                self._finished_at = time.monotonic()
                elapsed = self._finished_at - (self._started_at or self._finished_at)
            logger.info("embedding model ready after %.1fs", elapsed)
        except Exception as exc:  # noqa: BLE001 - a failed warmup is reportable state
            logger.warning("embedding model failed to warm: %s", exc)
            with self._lock:
                self._state = WarmupState.FAILED
                self._error = str(exc)[:500]
                self._finished_at = time.monotonic()

    def reset(self) -> None:
        """Allow a retry after a failure."""
        with self._lock:
            if self._state is WarmupState.FAILED:
                self._state = WarmupState.COLD
                self._error = None
                self._started_at = None
                self._finished_at = None


WARMER = ModelWarmer()
