"""per-paper cluster membership confidence

HDBSCAN reports how strongly each point belongs to the cluster it was assigned,
which is the honest answer to "how sure is this placement". It was being
discarded. Storing it lets the interface distinguish a paper at the centre of
its field from one sitting on a boundary, which matters most in exactly the
mixed clusters that are hardest to read.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projections") as batch:
        batch.add_column(sa.Column("cluster_probability", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projections") as batch:
        batch.drop_column("cluster_probability")
