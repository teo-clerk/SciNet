"""Serialised access to the GPU, and model residency accounting.

The target machine has 8188 MiB of VRAM and that is the binding constraint on
the whole pipeline. Marker+Surya needs roughly 3-4 GiB, the tagging LLM about
5.5, and the tier-2 vision model must be kept under 5.5 — no two of the large
models fit at once. The constraint is residency, not throughput, so the worker
is organised around *which model is loaded* rather than around parallelism.

Two rules follow, and both are enforced here:

1. One model resident at a time. Acquiring the slot for a different model
   evicts the incumbent first.
2. Work is batched by model. A caller holding the slot should drain every job
   needing that model before releasing it — a reload costs 5-15 s, which dwarfs
   the per-item work of most stages.

The cost of getting this wrong is not an error. Ollama serves an oversized
model from system RAM without complaint, roughly twenty times slower; the
``vram_mib is None`` case below is treated as unproven for exactly that reason.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app.core.models_registry import TOTAL_VRAM_MIB, ModelEntry

logger = logging.getLogger(__name__)


class GpuSlot:
    """The single GPU residency slot.

    Not a general resource pool: there is exactly one slot because there is
    exactly enough VRAM for one large model.
    """

    def __init__(self, total_vram_mib: int = TOTAL_VRAM_MIB) -> None:
        self._lock = threading.RLock()
        self._total = total_vram_mib
        self._resident: ModelEntry | None = None
        self._unload: Callable[[], None] | None = None

    @property
    def resident(self) -> ModelEntry | None:
        return self._resident

    def fits(self, entry: ModelEntry) -> bool:
        """Whether the model is *known* to fit.

        An unmeasured model returns False. Refusing to guess is deliberate: the
        alternative is a job that appears to hang for six minutes a page.
        """
        return entry.vram_mib is not None and entry.vram_mib <= self._total

    @contextmanager
    def hold(
        self,
        entry: ModelEntry,
        *,
        unload: Callable[[], None] | None = None,
        allow_unmeasured: bool = True,
    ) -> Iterator[ModelEntry]:
        """Take the slot for ``entry``, evicting whatever is resident.

        ``unload`` is how the caller frees its own model's memory. It runs when
        a *different* model needs the slot, not on every release, so consecutive
        jobs using the same model keep it warm.
        """
        if entry.vram_mib is not None and entry.vram_mib > self._total:
            raise RuntimeError(
                f"{entry.reference} needs ~{entry.vram_mib} MiB but only "
                f"{self._total} MiB exists; it would be served from RAM"
            )
        if entry.vram_mib is None and not allow_unmeasured:
            raise RuntimeError(
                f"{entry.reference} has no measured footprint; run "
                "scripts/download_models.py --measure"
            )

        with self._lock:
            if self._resident is not None and self._resident.key != entry.key:
                self._evict()

            self._resident = entry
            self._unload = unload
            logger.info("gpu slot -> %s (%s MiB)", entry.reference, entry.vram_mib)
            try:
                yield entry
            finally:
                # Deliberately still resident: the worker drains a whole stage
                # before moving on, and evicting here would reload the model for
                # every single paper.
                pass

    def _evict(self) -> None:
        incumbent = self._resident
        if incumbent is None:
            return
        logger.info("gpu slot: evicting %s", incumbent.reference)
        if self._unload is not None:
            try:
                self._unload()
            except Exception:  # noqa: BLE001 - eviction must never block progress
                logger.exception("failed to unload %s", incumbent.reference)
        self._resident = None
        self._unload = None

    def release(self) -> None:
        """Free the slot entirely. Called between pipeline stages."""
        with self._lock:
            self._evict()


GPU = GpuSlot()
