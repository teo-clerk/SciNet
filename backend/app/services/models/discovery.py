"""What the local Ollama stores actually hold.

Two servers can exist on one machine: the project's private instance (or the
override the user pointed it at) and the system-wide Ollama on 11434 that
most people already have. A model the user pulled globally months ago is a
routing candidate they already paid the download for — the Model Lab should
show it rather than pretend only the project's store exists.

Listing stays at ``/api/tags``: it already carries name, byte size and the
quantization level, and a per-model ``/api/show`` round-trip for context
lengths would turn one cheap request into N for a detail nobody filters on.
A server that is not running simply contributes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import Settings, get_settings
from app.core.model_store import PrivateOllama

#: Ollama's default port — where a system-wide install listens.
SYSTEM_OLLAMA_PORT = 11434


@dataclass(frozen=True)
class DiscoveredModel:
    reference: str
    #: "configured" = the server generation actually uses (private or
    #: override); "system" = the machine's default Ollama at 11434.
    server: str
    size_mib: int | None
    quantization: str | None


def _tags(url: str) -> list[dict]:
    try:
        response = httpx.get(f"{url}/api/tags", timeout=10.0)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - a down server holds nothing
        return []
    return response.json().get("models", []) or []


def discover(settings: Settings | None = None) -> list[DiscoveredModel]:
    settings = settings or get_settings()
    servers = [
        ("configured", PrivateOllama(settings)),
        ("system", PrivateOllama(settings, port=SYSTEM_OLLAMA_PORT)),
    ]

    found: list[DiscoveredModel] = []
    seen_urls: set[str] = set()
    for label, server in servers:
        # Under an override both constructions can point at the same URL;
        # listing it twice would show every model as its own duplicate.
        if server.url in seen_urls:
            continue
        seen_urls.add(server.url)
        for model in _tags(server.url):
            name = model.get("name")
            if not name:
                continue
            size = model.get("size")
            details = model.get("details") or {}
            found.append(
                DiscoveredModel(
                    reference=name,
                    server=label,
                    size_mib=int(size) // (1024 * 1024) if size else None,
                    quantization=details.get("quantization_level"),
                )
            )
    return found
