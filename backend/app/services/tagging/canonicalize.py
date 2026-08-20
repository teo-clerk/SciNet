"""Keeping the tag vocabulary from fragmenting.

Even with a closed list the model proposes new terms, and most proposals are
restatements of a tag that already exists — "graph-nets" for
"graph-neural-networks". Left alone these accumulate into a vocabulary where
filtering by one tag misses half the papers that belong to it.

Proposals are therefore compared to the existing vocabulary by embedding
similarity. A close match becomes an alias pointing at the canonical tag; only
a genuinely distinct term is kept, and even then it waits in a review queue
rather than joining the vocabulary silently.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Tag, TagKind, TagStatus
from app.services.tagging.taxonomy import MERGE_SIMILARITY, SEED_TAGS

logger = logging.getLogger(__name__)


def seed_vocabulary(session: Session) -> int:
    """Insert the seed tags. Idempotent, so it is safe on every startup."""
    existing = {slug for slug in session.scalars(select(Tag.slug))}
    added = 0
    for slug, label, kind in SEED_TAGS:
        if slug in existing:
            continue
        session.add(Tag(slug=slug, label=label, kind=kind, status=TagStatus.APPROVED))
        added += 1
    if added:
        session.flush()
        logger.info("seeded %d tag(s)", added)
    return added


def approved_slugs(session: Session) -> list[str]:
    """The vocabulary the model is allowed to choose from."""
    return list(
        session.scalars(
            select(Tag.slug)
            .where(Tag.status == TagStatus.APPROVED, Tag.canonical_id.is_(None))
            .order_by(Tag.slug)
        )
    )


def resolve(session: Session, slug: str) -> Tag | None:
    """Follow an alias to the tag it stands for."""
    tag = session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        return None
    if tag.canonical_id is not None:
        return session.get(Tag, tag.canonical_id)
    return tag


def register_proposal(
    session: Session,
    slug: str,
    *,
    embedder=None,
    similarity_threshold: float = MERGE_SIMILARITY,
) -> Tag:
    """Record a proposed tag, merging it into an existing one if it is a near
    duplicate.

    ``embedder`` maps a list of strings to a normalised matrix. Without one the
    proposal is simply queued for review — degrading to "ask a human" is the
    right failure mode for vocabulary decisions.
    """
    existing = session.scalar(select(Tag).where(Tag.slug == slug))
    if existing is not None:
        return existing

    canonical: Tag | None = None
    if embedder is not None:
        candidates = list(
            session.scalars(
                select(Tag).where(
                    Tag.status == TagStatus.APPROVED, Tag.canonical_id.is_(None)
                )
            )
        )
        if candidates:
            labels = [c.label for c in candidates]
            vectors = np.asarray(embedder([slug.replace("-", " "), *labels]))
            query, rest = vectors[0], vectors[1:]
            scores = rest @ query
            best = int(np.argmax(scores))
            if float(scores[best]) >= similarity_threshold:
                canonical = candidates[best]
                logger.info(
                    "merging proposed tag %r into %r (similarity %.2f)",
                    slug,
                    canonical.slug,
                    float(scores[best]),
                )

    tag = Tag(
        slug=slug,
        label=slug.replace("-", " ").title(),
        kind=TagKind.DOMAIN,
        canonical_id=canonical.id if canonical else None,
        status=TagStatus.MERGED if canonical else TagStatus.PENDING_REVIEW,
    )
    session.add(tag)
    session.flush()
    return tag
