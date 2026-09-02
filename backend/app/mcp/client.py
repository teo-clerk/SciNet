"""One httpx surface for every MCP tool.

Each call opens its own ``AsyncClient`` from an injectable factory. Per-call
clients cost a connection setup at human question-asking speeds — nothing —
and buy two things: no connection lifecycle to manage inside a long-lived
stdio server, and tests that swap the factory for one backed by
``httpx.ASGITransport`` so the whole stack runs in-process with no socket,
no lifespan, and no model warm-up thread.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0


class ApiDown(RuntimeError):
    """The API is not answering; the message says how to start it."""


class ApiClient:
    def __init__(
        self,
        base_url: str,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
        )

    async def get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """GET a JSON endpoint. Returns (status_code, decoded body).

        Status interpretation stays with the caller — a 503 from semantic
        search is a *warming* answer with an ETA, not a failure, and only the
        tool knows that.
        """
        try:
            async with self._factory() as http:
                res = await http.get(f"{self.base_url}{path}", params=params)
        except httpx.ConnectError as exc:
            raise ApiDown(
                f"the SciNet API is not answering at {self.base_url} — "
                f"start it with `uv run scinet-up` (or `uv run uvicorn "
                f"app.main:app` from backend/) and ask again"
            ) from exc
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, res.text
