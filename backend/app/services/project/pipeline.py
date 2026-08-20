"""Keeping the map up to date without rebuilding it.

The normal path is cheap: a new paper is placed with ``transform`` against the
stored reducer, in milliseconds, and marked provisional. A full refit happens
only when enough has changed to justify it — and when it does, the new layout is
Procrustes-aligned onto the old one before anyone sees it, so the map settles
rather than scrambling.

Swaps are atomic. A refit is computed into a *new* ``projection_runs`` row and
only becomes visible when ``is_active`` moves, so a reader never observes a
half-written layout and a crashed refit leaves the previous map intact.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.embed.store import VectorStore
from app.services.project.cluster import cluster_embeddings, top_terms
from app.services.project.procrustes import align_to, mean_displacement
from app.services.project.reducer import ProjectionModel, ProjectionParams
from app.services.project.skeleton import build_skeleton
from app.services.project.skeleton import save as save_skeleton

logger = logging.getLogger(__name__)

#: Refit once this share of the corpus was placed by transform() rather than fit.
TRANSFORMED_SHARE_TRIGGER = 0.20
#: Or once this many papers sit clearly off the fitted manifold.
OFF_MANIFOLD_COUNT_TRIGGER = 25
OFF_MANIFOLD_SCORE = 1.6


@dataclass
class ProjectionOutcome:
    run_id: int
    method: str
    refitted: bool
    placed: int
    total: int
    clusters: int
    mean_displacement: float | None = None
    reason: str = ""


def _model_path(settings: Settings, run_id: int):
    return settings.models_dir / "projections" / f"run_{run_id:05d}"


def active_run(session: Session):
    from app.models import ProjectionRun

    return session.scalar(
        select(ProjectionRun).where(ProjectionRun.is_active.is_(True))
    )


def should_refit(session: Session, run) -> tuple[bool, str]:
    """Decide whether the stored fit still represents the corpus."""
    from app.models import Projection

    if run is None:
        return True, "no active projection"

    total = session.scalar(
        select(Projection)
        .where(Projection.run_id == run.id)
        .with_only_columns(Projection.paper_id)
        .limit(1)
    )
    if total is None:
        return True, "active projection has no coordinates"

    rows = session.query(Projection).filter(Projection.run_id == run.id)
    count = rows.count()
    transformed = rows.filter(Projection.is_transformed.is_(True)).count()
    drifted = rows.filter(Projection.off_manifold > OFF_MANIFOLD_SCORE).count()

    if count and transformed / count > TRANSFORMED_SHARE_TRIGGER:
        return True, f"{transformed}/{count} papers placed incrementally"
    if drifted >= OFF_MANIFOLD_COUNT_TRIGGER:
        return True, f"{drifted} papers sit off the fitted manifold"
    return False, ""


def project_corpus(
    session: Session,
    settings: Settings,
    store: VectorStore,
    *,
    force_refit: bool = False,
) -> ProjectionOutcome:
    """Bring the projection up to date, refitting only when warranted."""
    from app.models import Cluster, Projection, ProjectionRun

    matrix, paper_ids = store.matrix()
    if not paper_ids:
        raise ValueError("no document vectors to project")

    run = active_run(session)
    refit, reason = should_refit(session, run)
    refit = refit or force_refit
    if force_refit and not reason:
        reason = "refit requested"

    # --- incremental path: place only what is missing --------------------
    if not refit and run is not None:
        placed = _place_new(session, settings, store, run, matrix, paper_ids)
        return ProjectionOutcome(
            run_id=run.id,
            method=run.method,
            refitted=False,
            placed=placed,
            total=len(paper_ids),
            clusters=session.query(Cluster).filter(Cluster.run_id == run.id).count(),
        )

    # --- full refit into a new, inactive run -----------------------------
    logger.info("refitting projection over %d papers (%s)", len(paper_ids), reason)
    params = ProjectionParams(min_umap_rows=settings.umap_min_papers)

    new_run = ProjectionRun(
        model_id=settings.embed_model,
        params_json=json.dumps({"reason": reason, "n": len(paper_ids)}),
        n_fit=len(paper_ids),
        method="pending",
        is_active=False,
    )
    session.add(new_run)
    session.flush()

    model = ProjectionModel(_model_path(settings, new_run.id), params)
    coords = model.fit(matrix)

    # Carry the previous layout's orientation forward so the map settles
    # instead of scrambling. Without this every refit is a new random rotation.
    displacement = None
    if run is not None:
        previous = _previous_coords(session, run.id, paper_ids)
        if previous is not None:
            shared_rows, reference = previous
            before = mean_displacement(coords[shared_rows], reference)
            coords = align_to(coords, reference, shared=shared_rows)
            displacement = mean_displacement(coords[shared_rows], reference)
            logger.info(
                "procrustes: mean node movement %.3f -> %.3f", before, displacement
            )

    model.coords = coords
    model.save()

    # The faint global graph the map is drawn over. Computed from the embedding
    # space alongside the fit, so it never has to be recomputed per request.
    save_skeleton(_model_path(settings, new_run.id), build_skeleton(matrix, paper_ids))
    new_run.method = model.method
    new_run.model_path = str(model.reducer_path)
    new_run.fit_matrix_path = str(model.matrix_path)

    session.add_all(
        Projection(
            run_id=new_run.id,
            paper_id=pid,
            x=float(c[0]),
            y=float(c[1]),
            z=float(c[2]),
            is_transformed=False,
            off_manifold=0.0,
        )
        for pid, c in zip(paper_ids, coords, strict=True)
    )
    session.flush()

    cluster_count = _write_clusters(session, new_run.id, matrix, paper_ids)

    # The swap. One statement deactivates every other run, so a reader either
    # sees the old layout or the new one, never a mixture.
    session.execute(
        update(ProjectionRun)
        .where(ProjectionRun.id != new_run.id)
        .values(is_active=False)
    )
    new_run.is_active = True
    session.add(new_run)

    return ProjectionOutcome(
        run_id=new_run.id,
        method=model.method,
        refitted=True,
        placed=len(paper_ids),
        total=len(paper_ids),
        clusters=cluster_count,
        mean_displacement=displacement,
        reason=reason,
    )


def _place_new(
    session: Session,
    settings: Settings,
    store: VectorStore,
    run,
    matrix: np.ndarray,
    paper_ids: list[int],
) -> int:
    """Add papers the active run has never seen, using the stored reducer."""
    from app.models import Projection

    known = {
        pid
        for pid in session.scalars(
            select(Projection.paper_id).where(Projection.run_id == run.id)
        )
    }
    missing = [(i, pid) for i, pid in enumerate(paper_ids) if pid not in known]
    if not missing:
        return 0

    model = ProjectionModel.load(_model_path(settings, run.id))
    rows = np.array([matrix[i] for i, _ in missing], dtype=np.float32)

    coords = model.transform(rows)
    drift = model.off_manifold_score(rows)

    session.add_all(
        Projection(
            run_id=run.id,
            paper_id=pid,
            x=float(c[0]),
            y=float(c[1]),
            z=float(c[2]),
            is_transformed=True,
            off_manifold=float(d),
        )
        for (_, pid), c, d in zip(missing, coords, drift, strict=True)
    )
    logger.info("placed %d new paper(s) without refitting", len(missing))
    return len(missing)


def _previous_coords(
    session: Session, run_id: int, paper_ids: list[int]
) -> tuple[np.ndarray, np.ndarray] | None:
    """Rows of the new layout that also existed in the old one, and their old
    positions."""
    from app.models import Projection

    old = {
        p.paper_id: (p.x, p.y, p.z)
        for p in session.scalars(select(Projection).where(Projection.run_id == run_id))
    }
    shared = [(i, pid) for i, pid in enumerate(paper_ids) if pid in old]
    if len(shared) < 4:
        return None
    rows = np.array([i for i, _ in shared])
    reference = np.array([old[pid] for _, pid in shared], dtype=np.float32)
    return rows, reference


def _write_clusters(
    session: Session, run_id: int, matrix: np.ndarray, paper_ids: list[int]
) -> int:
    from app.models import Cluster, PaperMeta, Projection

    assignments, probabilities, clusters = cluster_embeddings(matrix, paper_ids)
    if not clusters:
        return 0

    titles = {
        pid: title
        for pid, title in session.execute(
            select(PaperMeta.paper_id, PaperMeta.title)
        ).all()
    }

    from app.services.project import naming

    can_name = naming.available()
    if not can_name:
        logger.info("tagging model unavailable; clusters will be unnamed")

    for cluster in clusters:
        member_titles = [titles.get(p) or "" for p in cluster.paper_ids]
        terms = top_terms(member_titles)
        label = (
            naming.name_cluster(terms, [t for t in member_titles if t])
            if can_name
            else None
        )
        if label:
            logger.info(
                "cluster %d (%d papers) -> %r", cluster.label, cluster.size, label
            )

        row = Cluster(
            run_id=run_id,
            hdbscan_label=cluster.label,
            llm_label=label,
            size=cluster.size,
            centroid_json=json.dumps(cluster.centroid[:32]),
            top_terms_json=json.dumps(terms),
        )
        session.add(row)
        session.flush()
        session.execute(
            update(Projection)
            .where(
                Projection.run_id == run_id,
                Projection.paper_id.in_(cluster.paper_ids),
            )
            .values(cluster_id=row.id)
        )
        # Membership strength is per paper, not per cluster.
        for paper_id in cluster.paper_ids:
            session.execute(
                update(Projection)
                .where(
                    Projection.run_id == run_id,
                    Projection.paper_id == paper_id,
                )
                .values(cluster_probability=probabilities.get(paper_id))
            )
    return len(clusters)
