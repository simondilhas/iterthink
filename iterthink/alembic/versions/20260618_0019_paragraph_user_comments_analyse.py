"""paragraph_user_comments: analyse AI comment columns

Revision ID: 20260618_0019
Revises: 20260604_0018
Create Date: 2026-06-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260618_0019"
down_revision: Union[str, None] = "20260604_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paragraph_user_comments"):
        return
    cols = {c["name"] for c in inspector.get_columns("paragraph_user_comments")}
    if "source_id" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("source_id", sa.String(length=64), nullable=True),
        )
    if "symbol" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("symbol", sa.String(length=16), nullable=True),
        )
    if "details_json" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("details_json", sa.Text(), nullable=True),
        )
    if "overridden" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("overridden", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "override_comment" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("override_comment", sa.Text(), nullable=True),
        )
    indexes = {idx["name"] for idx in inspector.get_indexes("paragraph_user_comments")}
    if "uq_paragraph_user_comment_analyse" not in indexes:
        op.create_index(
            "uq_paragraph_user_comment_analyse",
            "paragraph_user_comments",
            ["content_version_id", "paragraph_index", "source_id"],
            unique=True,
            sqlite_where=sa.text("annotation_kind = 'analyse' AND source_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paragraph_user_comments"):
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("paragraph_user_comments")}
    if "uq_paragraph_user_comment_analyse" in indexes:
        op.drop_index(
            "uq_paragraph_user_comment_analyse",
            table_name="paragraph_user_comments",
        )
    cols = {c["name"] for c in inspector.get_columns("paragraph_user_comments")}
    for name in ("override_comment", "overridden", "details_json", "symbol", "source_id"):
        if name in cols:
            op.drop_column("paragraph_user_comments", name)
