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

QUERY_INSTRUCTION = (
    "Instruct: Given a search query, retrieve relevant scientific papers\nQuery: "
)
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


def encode_query(text: str, *, settings: Settings | None = None) -> np.ndarray:
    """Embed a search query, with the instruction prefix the model expects."""
    settings = settings or get_settings()
    model = load(settings)
    vector = model.encode(
        [QUERY_INSTRUCTION + text],
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
