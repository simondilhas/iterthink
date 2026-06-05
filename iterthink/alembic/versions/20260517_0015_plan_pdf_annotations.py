"""Plan PDF pin and revision-cloud columns on paragraph_user_comments.

Revision ID: 20260517_0015
Revises: 20260516_0014
Create Date: 2026-05-17

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260517_0015"
down_revision: Union[str, None] = "20260516_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paragraph_user_comments"):
        return
    cols = {c["name"] for c in inspector.get_columns("paragraph_user_comments")}

    if "annotation_kind" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column(
                "annotation_kind",
                sa.String(length=24),
                nullable=False,
                server_default="paragraph",
            ),
        )
    if "plan_page_index" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("plan_page_index", sa.Integer(), nullable=True),
        )
    if "plan_norm_x" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("plan_norm_x", sa.Float(), nullable=True),
        )
    if "plan_norm_y" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("plan_norm_y", sa.Float(), nullable=True),
        )
    if "geometry_json" not in cols:
        op.add_column(
            "paragraph_user_comments",
            sa.Column("geometry_json", sa.Text(), nullable=True),
        )

    bind.execute(
        sa.text(
            "UPDATE paragraph_user_comments SET annotation_kind = 'paragraph' "
            "WHERE annotation_kind IS NULL OR annotation_kind = ''"
        )
    )

    uqs = {c["name"] for c in inspector.get_unique_constraints("paragraph_user_comments")}
    if "uq_paragraph_user_comment_key" in uqs:
        with op.batch_alter_table("paragraph_user_comments") as batch_op:
            batch_op.drop_constraint("uq_paragraph_user_comment_key", type_="unique")

    indexes = {idx["name"] for idx in inspector.get_indexes("paragraph_user_comments")}
    if "uq_paragraph_user_comment_paragraph" not in indexes:
        op.create_index(
            "uq_paragraph_user_comment_paragraph",
            "paragraph_user_comments",
            ["document_id", "version_id", "paragraph_index"],
            unique=True,
            sqlite_where=sa.text("annotation_kind = 'paragraph'"),
        )
    if "ix_paragraph_user_comments_plan_ver" not in indexes:
        op.create_index(
            "ix_paragraph_user_comments_plan_ver",
            "paragraph_user_comments",
            ["document_id", "version_id", "annotation_kind"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("paragraph_user_comments"):
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("paragraph_user_comments")}
    if "ix_paragraph_user_comments_plan_ver" in indexes:
        op.drop_index("ix_paragraph_user_comments_plan_ver", table_name="paragraph_user_comments")
    if "uq_paragraph_user_comment_paragraph" in indexes:
        op.drop_index(
            "uq_paragraph_user_comment_paragraph",
            table_name="paragraph_user_comments",
        )
    uqs = {c["name"] for c in inspector.get_unique_constraints("paragraph_user_comments")}
    if "uq_paragraph_user_comment_key" not in uqs:
        op.create_unique_constraint(
            "uq_paragraph_user_comment_key",
            "paragraph_user_comments",
            ["document_id", "version_id", "paragraph_index"],
        )
    cols = {c["name"] for c in inspector.get_columns("paragraph_user_comments")}
    for name in ("geometry_json", "plan_norm_y", "plan_norm_x", "plan_page_index", "annotation_kind"):
        if name in cols:
            op.drop_column("paragraph_user_comments", name)
