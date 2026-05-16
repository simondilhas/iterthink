"""paragraph_user_comments: content_hash for stable paragraph anchoring

Revision ID: 20260516_0014
Revises: 20260514_0013
Create Date: 2026-05-16

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260516_0014"
down_revision: Union[str, None] = "20260514_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paragraph_user_comments"):
        return
    cols = {c["name"] for c in inspector.get_columns("paragraph_user_comments")}
    if "content_hash" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("content_hash", sa.String(length=64), nullable=True),
        )
    indexes = {idx["name"] for idx in inspector.get_indexes("paragraph_user_comments")}
    if "ix_paragraph_user_comments_doc_ver_hash" not in indexes:
        op.create_index(
            "ix_paragraph_user_comments_doc_ver_hash",
            "paragraph_user_comments",
            ["document_id", "version_id", "content_hash"],
            unique=False,
        )

    from iterthink import config
    from iterthink.compare.margin import split_paragraphs
    from iterthink.compare.paragraph_align import compute_hash

    rows = bind.execute(
        sa.text(
            """
            SELECT puc.id, puc.paragraph_index, dv.snapshot_relpath
            FROM paragraph_user_comments puc
            JOIN document_versions dv ON dv.id = puc.version_id
            WHERE puc.content_hash IS NULL
            """
        )
    ).fetchall()
    for row in rows:
        rid, pidx, relpath = int(row[0]), int(row[1]), str(row[2])
        try:
            snap_path = (config.STORE_DIR / relpath).resolve()
            if not snap_path.is_file():
                continue
            body = snap_path.read_text(encoding="utf-8")
            paras = split_paragraphs(body)
            if 0 <= pidx < len(paras):
                h = compute_hash(paras[pidx])
                bind.execute(
                    sa.text(
                        "UPDATE paragraph_user_comments SET content_hash = :h WHERE id = :id"
                    ),
                    {"h": h, "id": rid},
                )
        except OSError:
            continue


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paragraph_user_comments"):
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("paragraph_user_comments")}
    if "ix_paragraph_user_comments_doc_ver_hash" in indexes:
        op.drop_index(
            "ix_paragraph_user_comments_doc_ver_hash",
            table_name="paragraph_user_comments",
        )
    cols = {c["name"] for c in inspector.get_columns("paragraph_user_comments")}
    if "content_hash" in cols:
        op.drop_column("paragraph_user_comments", "content_hash")
