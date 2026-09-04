"""paper_insights — the plain-English layer

One row per work: the central question, the core argument and why it matters,
in language a reader from another field can follow, plus the claims the text
makes and the names it turns on. Replaced wholesale, carrying the model that
wrote it. Kept apart from paper_meta because it is a reading of the work
rather than a fact about it, and apart from the document vector because
embedding it would move every node on the map.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_insights",
        sa.Column(
            "paper_id",
            sa.Integer(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("genre", sa.String(length=32), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("argument", sa.Text(), nullable=True),
        sa.Column("significance", sa.Text(), nullable=True),
        sa.Column("claims_json", sa.Text(), nullable=True),
        sa.Column("entities_json", sa.Text(), nullable=True),
        sa.Column("grounded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("paper_insights")
