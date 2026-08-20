"""Local embedding via sentence-transformers.

Loaded lazily and cached: the model costs ~1.5 GiB of VRAM and several seconds
to load, so the worker drains the whole embed stage before releasing it.

Qwen3-Embedding is instruction-aware — it expects a short task prefix on
queries but not on documents. Getting that asymmetry wrong quietly degrades
retrieval without failing anything, so the two paths are separate functions
rather than a boolean argument.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from app.core.config import Settings, get_settings
from app.core.model_store import configure_environment

logger = logging.getLogger(__name__)

#: Queries are embedded on the CPU so interactive search never competes with
#: the worker for the card. See encode_query.
QUERY_DEVICE = "cpu"

#: Instruction-tuned embedders expect a task prefix on queries but not on
#: documents. Models outside that family were never trained on one and treat it
#: as literal text, so applying it blindly makes every query start with the same
#: forty tokens of noise. Keyed on the model rather than assumed.
QUERY_INSTRUCTIONS: dict[str, str] = {
    "qwen": (
        "Instruct: Given a search query, retrieve relevant scientific papers\nQuery: "
    ),
    "e5": "query: ",
    "bge": "Represent this sentence for searching relevant passages: ",
}


def query_instruction(model_id: str) -> str:
    """The prefix this model expects on a query, or none."""
    reference = model_id.lower()
    for marker, prefix in QUERY_INSTRUCTIONS.items():
        if marker in reference:
            return prefix
    # SPECTER-family models (scincl, specter2) take bare text: they were
    # trained on `title[SEP]abstract` with no instruction format at all.
    return ""


DEFAULT_BATCH_SIZE = 16


@lru_cache(maxsize=1)
def _model(model_id: str, device: str):
    from sentence_transformers import SentenceTransformer

    logger.info("loading embedding model %s on %s", model_id, device)
    return SentenceTransformer(model_id, device=device)


def load(settings: Settings | None = None):
    settings = settings or get_settings()
    # Ensures weights land in the project's cache, not the user's home.
    configure_environment(settings)
    return _model(settings.embed_model, settings.device)


def unload() -> None:
    """Free VRAM so the next stage's model can fit."""
    _model.cache_clear()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def encode_documents(
    texts: list[str],
    *,
    settings: Settings | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> np.ndarray:
    """Embed documents or chunks. Returns L2-normalised float32 rows."""
    settings = settings or get_settings()
    if not texts:
        return np.zeros((0, settings.embed_dim), dtype=np.float32)

    model = load(settings)
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def encode_query(
    text: str, *, settings: Settings | None = None, device: str | None = None
) -> np.ndarray:
    """Embed a search query, with the instruction prefix the model expects.

    ``device`` defaults to CPU rather than to the configured accelerator. The
    GPU belongs to the worker: it holds a single residency slot precisely
    because an 8 GiB card fits one model at a time, and the API is a separate
    process with no visibility into that. Loading the embedder here to serve one
    interactive query raced the worker for VRAM and returned
    "CUDA out of memory" to the user's search box.

    The trade is trivially in favour of the CPU. Batch-embedding a corpus wants
    an accelerator; embedding six words does not — it costs a few hundred
    milliseconds, which is well inside what a search feels like anyway.
    """
    settings = settings or get_settings()
    model = _model(settings.embed_model, device or QUERY_DEVICE)
    prefix = query_instruction(settings.embed_model)
    vector = model.encode(
        [prefix + text],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vector, dtype=np.float32)[0]


def embedding_dimension(settings: Settings | None = None) -> int:
    """Ask the model rather than trusting configuration."""
    settings = settings or get_settings()
    model = load(settings)
    # Renamed in sentence-transformers 6; keep working on either.
    getter = getattr(model, "get_embedding_dimension", None) or (
        model.get_sentence_embedding_dimension
    )
    return int(getter())
