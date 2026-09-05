"""GET /api/mcp — the connection recipe, and the tool list from the server.

The point of the endpoint is that nobody maintains a list of tools by hand:
the UI renders what the server reports, and the last test here holds the
docs to the same set, so a tool added or renamed in server.py fails CI until
docs/MCP.md says so too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import REPO_ROOT, Settings, get_settings
from app.main import create_app
from app.routers import mcp as mcp_router

TOOLS = {
    "search_library",
    "get_paper",
    "read_paper",
    "similar_papers",
    "list_regions",
    "region_details",
    "library_overview",
    "find_semantic_path",
    "query_quantities",
    "get_curriculum",
}


@pytest.fixture
def client_at(tmp_path, monkeypatch):
    """A client whose settings can be varied per test.

    No database and no lifespan: the recipe touches neither, and skipping the
    ``with`` keeps the embedding warm-up thread out of the test.
    """
    monkeypatch.delenv("SCINET_API_URL", raising=False)

    def make(**overrides) -> TestClient:
        settings = Settings(
            data_dir=tmp_path,
            library_dir=tmp_path / "library",
            markdown_dir=tmp_path / "markdown",
            vectors_dir=tmp_path / "vectors",
            models_dir=tmp_path / "models",
            db_path=tmp_path / "api.db",
            enrichment_enabled=False,
            **overrides,
        )
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(app)

    return make


@pytest.fixture
def client(client_at):
    return client_at()


# --- the recipe ---


def test_the_recipe_is_uv_running_the_console_script(client):
    body = client.get("/api/mcp").json()

    assert body["transport"] == "stdio"
    assert body["name"] == "scinet"
    assert body["command"] == "uv"
    assert body["args"][0] == "--directory"
    assert body["args"][1] == body["backend_dir"]
    assert body["args"][-2:] == ["run", "scinet-mcp"]
    backend_dir = Path(body["backend_dir"])
    assert backend_dir.is_absolute()
    assert backend_dir.is_dir()
    assert backend_dir.name == "backend"


def test_response_shape(client):
    body = client.get("/api/mcp").json()
    assert set(body) == {
        "transport",
        "name",
        "command",
        "args",
        "backend_dir",
        "api_url",
        "env",
        "available",
        "tools",
    }


def test_the_default_port_needs_no_env(client):
    body = client.get("/api/mcp").json()
    assert body["api_url"] == "http://127.0.0.1:8000"
    assert body["env"] == {}


def test_the_api_url_follows_the_configured_port(client_at):
    body = client_at(port=8123).get("/api/mcp").json()
    assert body["api_url"] == "http://127.0.0.1:8123"
    assert body["env"] == {"SCINET_API_URL": "http://127.0.0.1:8123"}


def test_scinet_api_url_wins(client, monkeypatch):
    monkeypatch.setenv("SCINET_API_URL", "http://127.0.0.1:9999")
    body = client.get("/api/mcp").json()
    assert body["api_url"] == "http://127.0.0.1:9999"
    assert body["env"] == {"SCINET_API_URL": "http://127.0.0.1:9999"}


# --- the tools ---


def test_the_tool_list_is_the_servers_own(client):
    body = client.get("/api/mcp").json()

    assert body["available"] is True
    assert {t["name"] for t in body["tools"]} == TOOLS
    for tool in body["tools"]:
        assert tool["description"], tool["name"]
        # cleandoc'd: no line still carries the function body's indentation
        assert not any(line.startswith(" ") for line in tool["description"].split("\n"))


def test_without_the_mcp_package_the_recipe_still_answers(client, monkeypatch):
    monkeypatch.setattr(mcp_router, "_server", lambda: None)
    res = client.get("/api/mcp")

    assert res.status_code == 200
    body = res.json()
    assert body["available"] is False
    assert body["tools"] == []
    assert body["args"][-2:] == ["run", "scinet-mcp"]


def test_the_docs_table_names_the_same_tools(client):
    """docs/MCP.md's tool table is held to the server's own list."""
    text = (REPO_ROOT / "docs" / "MCP.md").read_text()
    documented = set(re.findall(r"^\|\s*`([a-z_]+)\(", text, re.MULTILINE))
    served = {t["name"] for t in client.get("/api/mcp").json()["tools"]}
    assert documented == served
