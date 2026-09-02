"""quantities — measured values become queryable rows

Each row is one value from the prose, SI-normalised, carrying the verbatim
sentence it came from (chunks hold no offsets and are replaced wholesale, so
the sentence is the only provenance that survives). value_si is nullable: an
ambiguous unit is recorded for adjudication, never guessed at.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quantities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.Integer(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_ord", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("quantity_kind", sa.String(length=32), nullable=False),
        sa.Column("value_si", sa.Float(), nullable=True),
        sa.Column("unit_si", sa.String(length=32), nullable=True),
        sa.Column("value_original", sa.String(length=64), nullable=False),
        sa.Column("unit_original", sa.String(length=32), nullable=False),
        sa.Column("context_sentence", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extraction_source", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_quantities_paper", "quantities", ["paper_id"])
    op.create_index("ix_quantities_status", "quantities", ["status"])
    op.create_index(
        "ix_quantities_kind_value", "quantities", ["quantity_kind", "value_si"]
    )


def downgrade() -> None:
    op.drop_index("ix_quantities_kind_value", table_name="quantities")
    op.drop_index("ix_quantities_status", table_name="quantities")
    op.drop_index("ix_quantities_paper", table_name="quantities")
    op.drop_table("quantities")
