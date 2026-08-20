"""DTOs for system/health endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    pipeline_version: int


class PathsInfo(BaseModel):
    library_dir: str
    markdown_dir: str
    db_path: str


class ModelHealth(BaseModel):
    role: str
    reference: str
    quantization: str
    disk_mib: int
    #: None means the resident footprint has not been measured on this machine.
    vram_mib: int | None
    installed: bool
    #: None when vram_mib is unknown — unproven, not assumed to fit.
    fits_vram: bool | None
    note: str


class SystemInfo(BaseModel):
    """Surfaced in the UI so the operator can see what is actually configured."""

    version: str
    pipeline_version: int
    paths: PathsInfo
    embed_model: str
    llm_model: str
    vlm_model: str
    device: str
    enrichment_enabled: bool
    paper_count: int
    ready_count: int
    #: cold | warming | ready | failed — whether semantic search can answer yet.
    search_warmup: str = "cold"
    search_warmup_remaining: float | None = None
    models: list[ModelHealth] = []
