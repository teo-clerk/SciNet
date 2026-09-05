"""What an MCP client needs to reach this library, and what it gets."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class McpTool(BaseModel):
    name: str
    #: The tool's own docstring, dedented. Its first sentence is the blurb.
    description: str


class McpRecipe(BaseModel):
    """The connection recipe — a command, because the transport is stdio.

    The client spawns ``command args`` itself; there is no socket to report.
    ``env`` is empty unless the API is off its default port, in which case it
    pins ``SCINET_API_URL`` so the spawned server finds it.
    """

    transport: Literal["stdio"] = "stdio"
    name: str
    command: str
    args: list[str]
    backend_dir: str
    api_url: str
    env: dict[str, str]
    available: bool
    tools: list[McpTool]
