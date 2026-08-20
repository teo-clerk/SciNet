"""full-text index over chunks

SQLAlchemy models cannot declare FTS5 virtual tables, so the index the schema
always assumed existed was never actually created. This adds it as an
external-content table over `chunks` — the text is not duplicated, FTS5 reads
it back through the rowid — plus triggers so the index tracks writes, and a
backfill for chunks that already exist.

Revision ID: a1b2c3d4e5f6
Revises: 8cdb329ee0b8
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "8cdb329ee0b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `content=` makes this an external-content index: FTS5 stores only the
    # inverted index and fetches the original text from `chunks` by rowid, so a
    # 17k-chunk corpus does not pay for a second copy of itself.
    op.execute(
        """
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            text,
            section,
            content='chunks',
            content_rowid='id',
            tokenize='porter unicode61'
        )
        """
    )

    # External-content tables do not track their source automatically. Without
    # these the index silently drifts from the table it claims to describe.
    op.execute(
        """
        CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text, section)
            VALUES (new.id, new.text, new.section);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text, section)
            VALUES ('delete', old.id, old.text, old.section);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER chunks_fts_update AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text, section)
            VALUES ('delete', old.id, old.text, old.section);
            INSERT INTO chunks_fts(rowid, text, section)
            VALUES (new.id, new.text, new.section);
        END
        """
    )

    # Backfill anything ingested before the index existed.
    op.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS chunks_fts_update")
    op.execute("DROP TRIGGER IF EXISTS chunks_fts_delete")
    op.execute("DROP TRIGGER IF EXISTS chunks_fts_insert")
    op.execute("DROP TABLE IF EXISTS chunks_fts")
