"""One server of record for availability, generation, and unloading.

The split-brain this guards: with ``SCINET_OLLAMA_URL_OVERRIDE`` set,
generation honoured the override while ``PrivateOllama`` kept talking to the
private port — so ``available()`` checked one server's tags, ``tag_paper``
generated against another, tier 2's probe *spawned* a private server the
user had deliberately opted out of, and ``free_all_models`` unloaded models
from a server nothing was loaded on.
"""

from __future__ import annotations

import pytest

from app.core import gpu, model_store
from app.core.config import Settings
from app.core.model_store import PRIVATE_OLLAMA, PrivateOllama


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        data_dir=tmp_path,
        library_dir=tmp_path / "library",
        markdown_dir=tmp_path / "markdown",
        vectors_dir=tmp_path / "vectors",
        models_dir=tmp_path / "models",
        db_path=tmp_path / "t.db",
        **overrides,
    )


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


# --- the url is the server of record -----------------------------------------


def test_without_an_override_the_private_port_is_the_server(tmp_path) -> None:
    server = PrivateOllama(make_settings(tmp_path, ollama_port=11500))
    assert server.url == "http://127.0.0.1:11500"
    assert not server.is_override


def test_an_override_is_honoured_everywhere_not_just_in_generation(tmp_path) -> None:
    server = PrivateOllama(
        make_settings(tmp_path, ollama_url_override="http://127.0.0.1:11434")
    )
    assert server.url == "http://127.0.0.1:11434"
    assert server.is_override


def test_is_running_probes_the_override_port(tmp_path, monkeypatch) -> None:
    asked: list[tuple[str, int]] = []
    monkeypatch.setattr(
        model_store,
        "_port_is_open",
        lambda host, port: (asked.append((host, port)), False)[1],
    )
    PrivateOllama(
        make_settings(tmp_path, ollama_url_override="http://127.0.0.1:11434")
    ).is_running()
    assert asked == [("127.0.0.1", 11434)]


def test_start_under_an_override_spawns_nothing(tmp_path, monkeypatch) -> None:
    """The override means "use that server, not ours" — a probe that spawned a
    private instance anyway left two servers fighting over one card."""

    def forbidden(*args, **kwargs):
        raise AssertionError("start() must not spawn under an override")

    monkeypatch.setattr(model_store.subprocess, "Popen", forbidden)
    server = PrivateOllama(
        make_settings(tmp_path, ollama_url_override="http://127.0.0.1:11434")
    )
    server.start()  # returns quietly; the override server is somebody else's


# --- resident models ----------------------------------------------------------


def test_resident_models_reports_what_the_server_holds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        model_store.httpx,
        "get",
        lambda url, timeout: FakeResponse(
            {"models": [{"name": "qwen3:8b"}, {"name": "granite3.2-vision:2b"}]}
        ),
    )
    server = PrivateOllama(make_settings(tmp_path))
    assert server.resident_models() == ["qwen3:8b", "granite3.2-vision:2b"]


def test_a_down_server_holds_nothing(tmp_path, monkeypatch) -> None:
    def refuse(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(model_store.httpx, "get", refuse)
    assert PrivateOllama(make_settings(tmp_path)).resident_models() == []


# --- free_all_models unloads what is actually there ---------------------------


def test_free_all_models_unloads_what_ps_reports(monkeypatch) -> None:
    """Not a hardcoded (vlm_model, llm_model) pair: a model the router loaded
    under any other name was staying resident through every stage change."""
    unloaded: list[str] = []
    monkeypatch.setattr(
        PRIVATE_OLLAMA, "resident_models", lambda: ["some-other:7b", "qwen3:8b"]
    )
    monkeypatch.setattr(PRIVATE_OLLAMA, "unload", unloaded.append)
    gpu.free_all_models()
    assert unloaded == ["some-other:7b", "qwen3:8b"]


def test_free_all_models_survives_a_down_server(monkeypatch) -> None:
    def boom():
        raise OSError("refused")

    monkeypatch.setattr(PRIVATE_OLLAMA, "resident_models", boom)
    gpu.free_all_models()  # every step is fenced; nothing may raise


@pytest.mark.parametrize("payload", [{}, {"models": []}, {"models": [{}]}])
def test_odd_ps_payloads_never_break_the_listing(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(
        model_store.httpx, "get", lambda url, timeout: FakeResponse(payload)
    )
    assert PrivateOllama(make_settings(tmp_path)).resident_models() == []
