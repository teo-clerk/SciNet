"""The checkout must carry its own models.

The failure this guards against is silent and only shows up on someone else's
machine: a model library that was never told where to write puts its weights in
the user's home directory, everything works locally, and the zipped project
turns out to be hollow when it is unpacked somewhere else.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.core.config import Settings
from app.core.model_store import configure_environment
from app.services.embed import encoder

#: Every variable that decides where a library writes weights or generated
#: data. Named here as well as in configure_environment so that adding one
#: without pointing it into the project fails a test rather than nothing.
CACHE_VARIABLES = (
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "HF_DATASETS_CACHE",
    "SENTENCE_TRANSFORMERS_HOME",
    "TIKTOKEN_CACHE_DIR",
    "NLTK_DATA",
    "MPLCONFIGDIR",
    "MODEL_CACHE_DIR",
    "TORCH_HOME",
)


@pytest.fixture
def clean_env():
    """configure_environment writes to the real os.environ; put it back."""
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "data" / "models")


def test_every_cache_variable_points_inside_the_project(settings, clean_env):
    applied = configure_environment(settings)

    for name in CACHE_VARIABLES:
        assert name in applied, f"{name} is not configured"
        assert applied[name].startswith(str(settings.models_dir)), (
            f"{name} points outside the project: {applied[name]}"
        )


def test_configuration_reaches_the_process_environment(settings, clean_env):
    # Setting a dict is not enough — the libraries read os.environ, and they
    # read it at import time.
    configure_environment(settings)

    assert os.environ["HF_HOME"].startswith(str(settings.models_dir))


def test_loading_a_model_configures_the_cache_first(settings, clean_env, monkeypatch):
    """The API's warmup reached the model loader directly and skipped this.

    The result was the embedding model downloading into ~/.cache/huggingface
    from the API process while the worker kept an identical copy in the
    project. Nothing failed, so nothing said so.
    """
    seen: dict[str, str] = {}

    class Stub:
        def encode(self, texts, **_kwargs):
            seen["HF_HOME"] = os.environ.get("HF_HOME", "")
            return np.zeros((len(texts), settings.embed_dim), dtype=np.float32)

    monkeypatch.setattr(encoder, "_model", lambda *_args: Stub())
    os.environ.pop("HF_HOME", None)

    encoder.encode_query("anything", settings=settings)

    assert seen["HF_HOME"].startswith(str(settings.models_dir))


def test_embedding_documents_configures_the_cache_too(settings, clean_env, monkeypatch):
    seen: dict[str, str] = {}

    class Stub:
        def encode(self, texts, **_kwargs):
            seen["HF_HOME"] = os.environ.get("HF_HOME", "")
            return np.zeros((len(texts), settings.embed_dim), dtype=np.float32)

    monkeypatch.setattr(encoder, "_model", lambda *_args: Stub())
    os.environ.pop("HF_HOME", None)

    encoder.encode_documents(["some text"], settings=settings)

    assert seen["HF_HOME"].startswith(str(settings.models_dir))


def test_the_api_configures_the_cache_before_warming(settings, clean_env, monkeypatch):
    """The API loads the embedding model too, for search."""
    import app.main as main
    from app.core.warmup import WARMER

    order: list[str] = []
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main,
        "configure_environment",
        lambda s: (order.append("configure"), {"HF_HOME": str(s.models_dir)})[1],
    )
    monkeypatch.setattr(WARMER, "start", lambda: order.append("warm"))

    from fastapi import FastAPI

    async def drive():
        async with main.lifespan(FastAPI()):
            pass

    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(drive())

    assert order == ["configure", "warm"]
