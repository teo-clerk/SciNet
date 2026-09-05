"""The MCP connection recipe, described by asking the server itself.

The dialog in the UI, the docs and the code used to be three places a tool
could be named. Now the tool list is ``build_server().list_tools()`` and the
docs table is tested against this endpoint. Nothing here opens a socket: the
server object is built once and only introspected, and the ``mcp`` import is
lazy so the API's startup does not pay for it.
"""

from __future__ import annotations

import functools
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from app.core.config import REPO_ROOT, Settings, get_settings
from app.mcp.client import default_base_url
from app.schemas.mcp import McpRecipe, McpTool

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

router = APIRouter(prefix="/api", tags=["mcp"])

SERVER_NAME = "scinet"
CONSOLE_SCRIPT = "scinet-mcp"
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_API_URL = f"http://127.0.0.1:{Settings.model_fields['port'].default}"


@functools.cache
def _server() -> FastMCP | None:
    """The server object, built once and never run.

    ``mcp`` is a hard dependency, so ``None`` should not happen — but the
    recipe is useful even when the tool list is not, so the import failing
    degrades the answer instead of breaking it.
    """
    try:
        from app.mcp.server import build_server
    except ImportError:
        return None
    return build_server()


def recipe_args(backend_dir: Path = BACKEND_DIR) -> list[str]:
    """What an MCP client runs: uv, in backend/, the console script."""
    return ["--directory", str(backend_dir), "run", CONSOLE_SCRIPT]


def server_env(api_url: str) -> dict[str, str]:
    """Pin the API address only when it is not the default.

    The spawned server reads the same ``.env`` the API does, so in the default
    case an empty env keeps the snippet identical to the one in docs/MCP.md.
    """
    if api_url == DEFAULT_API_URL:
        return {}
    return {"SCINET_API_URL": api_url}


@router.get("/mcp", response_model=McpRecipe)
async def mcp_recipe(settings: Settings = Depends(get_settings)) -> McpRecipe:
    server = _server()
    tools: list[McpTool] = []
    if server is not None:
        # FastMCP stores the raw ``__doc__``; continuation lines carry the
        # function body's indentation until cleandoc removes it.
        tools = [
            McpTool(name=t.name, description=inspect.cleandoc(t.description or ""))
            for t in await server.list_tools()
        ]
    api_url = default_base_url(settings)
    return McpRecipe(
        name=SERVER_NAME,
        command="uv",
        args=recipe_args(),
        backend_dir=str(BACKEND_DIR),
        api_url=api_url,
        env=server_env(api_url),
        available=server is not None,
        tools=tools,
    )
