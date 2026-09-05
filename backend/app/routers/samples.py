"""Sample libraries: a map for someone who has nothing to drop in yet.

The first-run screen invites the reader to drop in papers, books and essays.
A reader who came to see what the map does may have none to hand, so the
repository ships one small library of public-domain texts, and this router
installs it — copies the files into the library folder and registers them,
exactly as the upload endpoint does for files a reader drops in. The pipeline
takes it from there.

Nothing here opens a socket. A sample the repository cannot carry — the
open-preprint corpus, whose files belong to their authors — is listed with the
command that fetches it, run by the reader on their own machine, and answers
409 if asked to install itself. That keeps the one rule the privacy story
rests on: the application never fetches anything; ``egress_log`` stays empty.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, Settings, get_settings
from app.core.db import get_db
from app.core.events import BROKER
from app.core.paths import SUPPORTED_EXTENSIONS, resolve_within
from app.models import Paper
from app.services.ingest.hashing import hash_file
from app.services.ingest.registrar import Registration, register_document

router = APIRouter(prefix="/api/samples", tags=["samples"])

#: Where the bundled samples live. A module constant rather than a setting:
#: they ship with the code, not with a library.
SAMPLES_DIR = REPO_ROOT / "demo" / "samples"
#: A sample's folder name is also its URL segment; it is never used as a path
#: without first being matched against the folders that actually exist.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: Files that describe the sample rather than belong to it.
NOT_DOCUMENTS = {"sample.json", "sources.lock.json", "SOURCES.md", "README.md"}


class SampleOut(BaseModel):
    name: str
    title: str
    blurb: str
    #: ``bundled`` installs from the repository; ``fetch`` names a command.
    kind: str
    documents: int
    #: True once any of the sample's files is in the library, by content hash.
    installed: bool
    command: str | None = None


class InstallResult(BaseModel):
    name: str
    queued: int
    duplicates: int


def _folder(name: str) -> Path:
    if not NAME_RE.match(name) or not (SAMPLES_DIR / name).is_dir():
        raise HTTPException(404, "no such sample")
    return resolve_within(SAMPLES_DIR / name, SAMPLES_DIR)


def _manifest(folder: Path) -> dict | None:
    path = folder / "sample.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _documents(folder: Path) -> list[Path]:
    return sorted(
        p
        for p in folder.iterdir()
        if p.is_file()
        and p.name not in NOT_DOCUMENTS
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _installed(db: Session, documents: list[Path]) -> bool:
    if not documents:
        return False
    # One hash is enough to say "this sample is in the library": a partial
    # install is still an install, and hashing forty small files is cheap.
    digests = [hash_file(p) for p in documents[:8]]
    return (
        db.scalar(select(Paper.id).where(Paper.content_sha256.in_(digests)).limit(1))
        is not None
    )


def _describe(db: Session, folder: Path) -> SampleOut | None:
    manifest = _manifest(folder)
    if manifest is None:
        return None
    documents = _documents(folder)
    kind = str(manifest.get("kind") or "bundled")
    return SampleOut(
        name=folder.name,
        title=str(manifest.get("title") or folder.name),
        blurb=str(manifest.get("blurb") or ""),
        kind=kind,
        documents=int(manifest.get("documents") or len(documents)),
        installed=_installed(db, documents) if kind == "bundled" else False,
        command=str(manifest["command"]) if manifest.get("command") else None,
    )


@router.get("", response_model=list[SampleOut])
def list_samples(db: Session = Depends(get_db)) -> list[SampleOut]:
    if not SAMPLES_DIR.is_dir():
        return []
    described = (
        _describe(db, folder)
        for folder in sorted(SAMPLES_DIR.iterdir())
        if folder.is_dir() and NAME_RE.match(folder.name)
    )
    return [sample for sample in described if sample is not None]


@router.post("/{name}/install", response_model=InstallResult)
def install_sample(
    name: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> InstallResult:
    """Copy a bundled sample into the library and queue it.

    The same precedent as ``/api/papers/upload``: the API creates the Paper
    rows and the parse jobs; the worker does everything after that. Copying
    rather than registering in place keeps the library folder the one place
    every document lives, so cleanup, quarantine and the watcher all see it.
    Re-installing is harmless — registration deduplicates by content hash.
    """
    folder = _folder(name)
    manifest = _manifest(folder) or {}
    if manifest.get("kind", "bundled") != "bundled":
        raise HTTPException(
            409,
            {
                "message": "this sample is fetched by a script you run, not by the app",
                "command": manifest.get("command"),
            },
        )

    settings.ensure_dirs()
    destination_dir = resolve_within(
        settings.library_dir / "samples" / name, settings.library_dir
    )
    destination_dir.mkdir(parents=True, exist_ok=True)

    queued = duplicates = 0
    for source in _documents(folder):
        destination = destination_dir / source.name
        if not destination.exists():
            shutil.copyfile(source, destination)
        result = register_document(db, destination)
        if result.outcome is Registration.CREATED:
            queued += 1
        else:
            duplicates += 1
    db.commit()

    BROKER.publish(
        "upload.batch", accepted=queued + duplicates, queued=queued, rejected=0
    )
    return InstallResult(name=name, queued=queued, duplicates=duplicates)
