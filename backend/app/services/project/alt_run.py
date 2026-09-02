"""A second opinion: the same corpus projected under a different embedder.

The schema was built for this without knowing it. ``ProjectionRun`` carries
``model_id`` and keeps inactive runs on disk; the vector store is keyed by
model slug; ``procrustes.align_to`` maps any layout onto any other. What was
missing was only the act: encode the active run's papers under model B, fit,
align onto the active layout, store as an *inactive* run. The active map
never moves — the M2 invariant holds by construction, because nothing here
touches the active run's rows.

The result is a map the UI can morph to: nodes that travel far under the
slider are exactly the papers the two models disagree about, which is a
diagnostic no benchmark table can draw.

Alt vectors get their own store but **no DocVector rows** — that table is
the primary model's index (one row per paper), and the alt store's own
header already records model and dimension.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MarkdownDoc, PaperMeta, Projection, ProjectionRun
from app.services.embed.chunker import document_text
from app.services.embed.store import VectorStore
from app.services.project.procrustes import align_to
from app.services.project.reducer import ProjectionModel, ProjectionParams

logger = logging.getLogger(__name__)

#: (texts, reference, settings) -> float32 matrix. Injectable so tests never
#: load a real model and the script can choose the device.
EncodeFn = Callable[[list[str], str, Settings], np.ndarray]


@dataclass(frozen=True)
class AltRunResult:
    run_id: int
    model_id: str
    rows: int
    dim: int
    #: Mean node displacement against the active layout, after alignment —
    #: the disagreement number the morph view visualises.
    mean_displacement: float


def _default_encode(texts: list[str], reference: str, settings: Settings) -> np.ndarray:
    """CPU by default: the script may run beside a busy worker, and a
    second embedder fighting the pipeline for VRAM is the exact failure
    this codebase is organised to prevent."""
    from app.services.embed import encoder

    model = encoder._prepared(reference, "cpu", settings)
    return np.asarray(
        model.encode(
            texts,
            batch_size=16,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ),
        dtype=np.float32,
    )


def _doc_text(session: Session, paper_id: int) -> str:
    meta = session.get(PaperMeta, paper_id)
    doc = session.get(MarkdownDoc, paper_id)
    markdown = ""
    if doc is not None:
        from pathlib import Path

        path = Path(doc.md_path)
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
    text = document_text(
        title=meta.title if meta else None,
        abstract=meta.abstract if meta else None,
        summary=meta.summary if meta else None,
        markdown=markdown or None,
    )
    return text if text.strip() else markdown[:2000]


def store_slug(model_id: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def build_alt_run(
    session: Session,
    settings: Settings,
    model_id: str,
    *,
    encode: EncodeFn | None = None,
) -> AltRunResult:
    """Encode, fit, align, persist — one inactive run under ``model_id``."""
    active = session.scalar(
        select(ProjectionRun).where(ProjectionRun.is_active.is_(True))
    )
    if active is None:
        raise RuntimeError("no active projection — build the map first")

    rows = session.execute(
        select(Projection.paper_id, Projection.x, Projection.y, Projection.z)
        .where(Projection.run_id == active.id)
        .order_by(Projection.paper_id)
    ).all()
    if not rows:
        raise RuntimeError("the active projection has no coordinates")

    paper_ids = [r.paper_id for r in rows]
    reference = np.array([[r.x, r.y, r.z] for r in rows], dtype=np.float32)

    texts = [_doc_text(session, pid) for pid in paper_ids]
    matrix = (encode or _default_encode)(texts, model_id, settings)
    if matrix.shape[0] != len(paper_ids):
        raise RuntimeError("encoder returned a different number of rows")
    dim = int(matrix.shape[1])

    # The alt vectors persist in their own store so similarity-space tools
    # can compare the two models later; its header records model and dim.
    store = VectorStore(
        settings.vectors_dir / f"doc_vectors__{store_slug(model_id)}",
        dim=dim,
        model_id=model_id,
    )
    store.add_many(list(zip(paper_ids, matrix, strict=True)))
    store.flush()

    params = ProjectionParams()
    run = ProjectionRun(
        model_id=model_id,
        params_json=json.dumps({"alt_of_run": active.id}),
        n_fit=len(paper_ids),
        method="pending",
        is_active=False,
    )
    session.add(run)
    session.flush()

    base = settings.models_dir / "projections" / f"run_{run.id:05d}"
    model = ProjectionModel(base, params)
    coords = model.fit(matrix)
    aligned = align_to(coords, reference).astype(np.float32)
    model.coords = aligned
    model.save()

    run.method = model.method or "umap"
    run.model_path = str(model.reducer_path)
    run.fit_matrix_path = str(model.matrix_path)
    session.add_all(
        Projection(
            run_id=run.id,
            paper_id=pid,
            x=float(aligned[i, 0]),
            y=float(aligned[i, 1]),
            z=float(aligned[i, 2]),
        )
        for i, pid in enumerate(paper_ids)
    )
    session.flush()

    displacement = float(np.linalg.norm(aligned - reference, axis=1).mean())
    logger.info(
        "alt run %d (%s): %d rows, mean displacement %.2f vs active run %d",
        run.id,
        model_id,
        len(paper_ids),
        displacement,
        active.id,
    )
    return AltRunResult(run.id, model_id, len(paper_ids), dim, displacement)
