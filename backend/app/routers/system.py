"""Health and configuration introspection."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.preflight import check_models
from app.core.warmup import WARMER
from app.models import Paper, PaperStatus
from app.schemas.system import (
    HealthResponse,
    ModelHealth,
    PathsInfo,
    SystemInfo,
)
from app.services.project.scaling import MIN_ROWS_TO_CLUSTER

router = APIRouter(prefix="/api", tags=["system"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=VERSION,
        pipeline_version=settings.pipeline_version,
    )


@router.get("/system", response_model=SystemInfo)
def system_info(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SystemInfo:
    warmup = WARMER.status()
    total = db.scalar(select(func.count()).select_from(Paper)) or 0
    ready = (
        db.scalar(
            select(func.count())
            .select_from(Paper)
            .where(Paper.status == PaperStatus.READY)
        )
        or 0
    )
    return SystemInfo(
        version=VERSION,
        pipeline_version=settings.pipeline_version,
        paths=PathsInfo(
            library_dir=str(settings.library_dir),
            markdown_dir=str(settings.markdown_dir),
            db_path=str(settings.db_path),
        ),
        embed_model=settings.embed_model,
        llm_model=settings.llm_model,
        vlm_model=settings.vlm_model,
        device=settings.device,
        enrichment_enabled=settings.enrichment_enabled,
        paper_count=total,
        ready_count=ready,
        search_warmup=warmup.state.value,
        search_warmup_remaining=warmup.estimated_remaining,
        regions_min_papers=MIN_ROWS_TO_CLUSTER,
        models=[
            ModelHealth(
                role=m.entry.role.value,
                reference=m.entry.reference,
                quantization=m.entry.quantization,
                disk_mib=m.entry.disk_mib,
                vram_mib=m.entry.vram_mib,
                installed=m.installed,
                fits_vram=m.fits_vram,
                note=m.note,
            )
            for m in check_models(settings)
        ],
    )
