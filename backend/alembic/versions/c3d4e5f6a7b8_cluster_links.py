"""cluster overviews and inter-cluster links

A cluster label answers "what is this region"; an overview answers "what is in
it and why these papers sit together". And a map of separate regions says
nothing about how the regions relate, which is most of what a reader of a
cross-disciplinary library wants to know.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clusters") as batch:
        batch.add_column(sa.Column("llm_overview", sa.Text(), nullable=True))

    op.create_table(
        "cluster_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("projection_runs.id", ondelete="CASCADE"),
        ),
        # Ordered so a pair appears once: source_id < target_id.
        sa.Column(
            "source_id", sa.Integer(), sa.ForeignKey("clusters.id", ondelete="CASCADE")
        ),
        sa.Column(
            "target_id", sa.Integer(), sa.ForeignKey("clusters.id", ondelete="CASCADE")
        ),
        #: Cosine similarity between the two cluster centroids, in embedding
        #: space — never on the rendered coordinates.
        sa.Column("similarity", sa.Float(), nullable=False),
        #: Papers sitting between the two regions: the evidence for the link.
        sa.Column("bridge_paper_ids", sa.Text(), nullable=True),
        sa.Column("shared_terms", sa.Text(), nullable=True),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cluster_links_run", "cluster_links", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_cluster_links_run", table_name="cluster_links")
    op.drop_table("cluster_links")
    with op.batch_alter_table("clusters") as batch:
        batch.drop_column("llm_overview")
