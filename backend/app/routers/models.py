"""The Model Lab's API: hardware, the catalog, discovery, pins, residency.

One GET carries everything the panel shows — probed hardware, the profile
catalog (seeded on first read; seeding is idempotent and writes only rows
that are missing), models discovered in both Ollama stores, the active pins,
what each task would resolve to right now, and what is actually resident on
the card. Pins are set with POST rather than PUT deliberately: CORS is
locked to GET/POST/DELETE, and widening it for one verb bought nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import hardware
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.model_router import PINNABLE, TaskKind, clear_pin, pins, resolve, set_pin
from app.core.model_store import PRIVATE_OLLAMA
from app.models import ModelProfile
from app.services.models.discovery import discover
from app.services.models.profiles import seed_profiles

router = APIRouter(prefix="/api/models", tags=["models"])


class HardwareInfo(BaseModel):
    vram_total_mib: int | None
    gpu_name: str | None
    ram_total_mib: int | None
    disk_free_gib: float | None
    vram_budget_mib: int


class ProfileInfo(BaseModel):
    reference: str
    runtime: str
    role: str
    disk_mib: int | None
    vram_mib: int | None
    tok_per_s: float | None
    embed_dim: int | None
    verdict: str
    source: str
    notes: str | None
    #: Computed against *this* card's budget at read time — the row's verdict
    #: records the measurement outcome and travels with the checkout.
    fits_here: bool | None


class DiscoveredInfo(BaseModel):
    reference: str
    server: str
    size_mib: int | None
    quantization: str | None
    in_catalog: bool


class ModelsOverview(BaseModel):
    hardware: HardwareInfo
    profiles: list[ProfileInfo]
    discovered: list[DiscoveredInfo]
    pins: dict[str, str]
    #: task value -> the reference resolve() answers right now.
    tasks: dict[str, str]
    pinnable: list[str]
    #: What /api/ps reports on the card this moment — the live gauge.
    resident: list[str]


class PinRequest(BaseModel):
    reference: str


@router.get("", response_model=ModelsOverview)
def models_overview(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ModelsOverview:
    seed_profiles(db)
    db.commit()

    report = hardware.probe(settings.models_dir)
    budget = hardware.vram_budget_mib()

    profiles = [
        ProfileInfo(
            reference=p.reference,
            runtime=p.runtime,
            role=p.role,
            disk_mib=p.disk_mib,
            vram_mib=p.vram_mib,
            tok_per_s=p.tok_per_s,
            embed_dim=p.embed_dim,
            verdict=p.verdict,
            source=p.source,
            notes=p.notes,
            fits_here=None if p.vram_mib is None else p.vram_mib <= budget,
        )
        for p in db.scalars(
            select(ModelProfile).order_by(ModelProfile.role, ModelProfile.reference)
        )
    ]
    catalog = {p.reference for p in profiles}

    return ModelsOverview(
        hardware=HardwareInfo(
            vram_total_mib=report.vram_total_mib,
            gpu_name=report.gpu_name,
            ram_total_mib=report.ram_total_mib,
            disk_free_gib=report.disk_free_gib,
            vram_budget_mib=budget,
        ),
        profiles=profiles,
        discovered=[
            DiscoveredInfo(
                reference=d.reference,
                server=d.server,
                size_mib=d.size_mib,
                quantization=d.quantization,
                in_catalog=d.reference in catalog,
            )
            for d in discover(settings)
        ],
        pins=pins(db),
        tasks={task.value: resolve(task, settings, db) for task in TaskKind},
        pinnable=[task.value for task in PINNABLE],
        resident=PRIVATE_OLLAMA.resident_models(),
    )


def _task_or_404(task_value: str) -> TaskKind:
    try:
        return TaskKind(task_value)
    except ValueError as exc:
        raise HTTPException(404, f"no task named {task_value!r}") from exc


@router.post("/pins/{task_value}")
def set_task_pin(
    task_value: str,
    body: PinRequest,
    db: Session = Depends(get_db),
) -> dict:
    task = _task_or_404(task_value)
    if task not in PINNABLE:
        raise HTTPException(409, f"{task.value} is not pin-routable in v1")
    set_pin(db, task, body.reference)
    db.commit()
    return {"task": task.value, "pinned": body.reference}


@router.delete("/pins/{task_value}")
def clear_task_pin(
    task_value: str,
    db: Session = Depends(get_db),
) -> dict:
    task = _task_or_404(task_value)
    clear_pin(db, task)
    db.commit()
    return {"task": task.value, "pinned": None}
