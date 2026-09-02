"""model profiles — measured facts live in the database

The static registry ships the reference machine's measurements; this table is
what the running system learns on *this* machine: measured footprints,
discovered models, benchmark scores. Before it, measurement ended with a
script printing "update vram_mib in models_registry.py by hand".

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_profiles",
        sa.Column("reference", sa.String(length=128), primary_key=True),
        sa.Column("runtime", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("disk_mib", sa.Integer(), nullable=True),
        sa.Column("vram_mib", sa.Integer(), nullable=True),
        sa.Column("tok_per_s", sa.Float(), nullable=True),
        sa.Column("embed_dim", sa.Integer(), nullable=True),
        sa.Column("bench_json", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_measured_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_model_profiles_verdict", "model_profiles", ["verdict"])


def downgrade() -> None:
    op.drop_index("ix_model_profiles_verdict", table_name="model_profiles")
    op.drop_table("model_profiles")
