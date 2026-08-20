"""Application settings, loaded from environment / .env.

Every path is resolved to an absolute path at construction time so the worker
and the API agree on locations regardless of their working directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SCINET_",
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- paths ----
    data_dir: Path = REPO_ROOT / "data"
    library_dir: Path = REPO_ROOT / "data" / "library"
    markdown_dir: Path = REPO_ROOT / "data" / "markdown"
    vectors_dir: Path = REPO_ROOT / "data" / "vectors"
    models_dir: Path = REPO_ROOT / "data" / "models"
    db_path: Path = REPO_ROOT / "data" / "scinet.db"

    # ---- api ----
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- local ai ----
    # SciNet runs its own Ollama on its own port, reading models from
    # ``models_dir``. It deliberately does not use the system instance on
    # 11434, so the project never depends on what is installed globally.
    ollama_port: int = 11500
    ollama_keep_alive: str = "60s"
    #: Set to reuse an already-running server instead of starting a private one.
    #: Leave empty for the isolated default.
    ollama_url_override: str = ""

    #: Tier 1 (Marker + Surya) is off by default. Measured on a 300-paper
    #: corpus it saved 3.6 minutes in total — 99.7% of real papers have a
    #: usable text layer and never reach it — while a single escalating paper
    #: cost over six minutes of retry storms and left llama-server processes
    #: holding VRAM. The router falls through to tier 2 cleanly when it is off.
    #: Turn it on for a scan-heavy library, where the economics reverse.
    tier1_enabled: bool = False

    #: Per-call ceiling for tier 1. A degenerate page can otherwise occupy the
    #: GPU for many minutes; one fixture was measured at 546 s for a single
    #: page. Exceeding this raises, and the router falls through to tier 2.
    tier1_timeout_seconds: int = 90
    tier1_max_layout_tokens: int = 3072
    #: Marker issues several inference calls per document (layout, recognition,
    #: tables), so the per-call limit above does not bound a document. The
    #: wall-clock budget for one document is this many times that limit.
    tier1_calls_per_document: int = 3

    #: Vulkan device for tier 1's llama.cpp server. Empty means "let llama.cpp
    #: choose", which on hybrid-graphics machines picks the integrated GPU.
    llamacpp_device: str = "Vulkan1"

    llm_model: str = "qwen3:8b"
    vlm_model: str = "granite3.2-vision:2b"
    embed_model: str = "malteos/scincl"
    embed_dim: int = 768
    llm_num_ctx: int = 8192
    device: str = "cuda"

    # ---- privacy ----
    enrichment_enabled: bool = False
    enrichment_email: str = ""

    # ---- pipeline ----
    pipeline_version: int = 1
    tier0_workers: int = 4
    umap_min_papers: int = Field(default=200, ge=10)

    @field_validator(
        "data_dir",
        "library_dir",
        "markdown_dir",
        "vectors_dir",
        "models_dir",
        "db_path",
        mode="after",
    )
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @property
    def ollama_url(self) -> str:
        return self.ollama_url_override or f"http://127.0.0.1:{self.ollama_port}"

    @property
    def ollama_models_dir(self) -> Path:
        return self.models_dir / "ollama"

    @property
    def hf_cache_dir(self) -> Path:
        return self.models_dir / "hf"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.library_dir,
            self.markdown_dir,
            self.vectors_dir,
            self.models_dir,
            self.ollama_models_dir,
            self.hf_cache_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
