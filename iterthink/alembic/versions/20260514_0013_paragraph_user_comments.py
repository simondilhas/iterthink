"""paragraph_user_comments for Review paragraph notes

Revision ID: 20260514_0013
Revises: 20260511_0012
Create Date: 2026-05-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260514_0013"
down_revision: Union[str, None] = "20260511_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("paragraph_user_comments"):
        return

    op.create_table(
        "paragraph_user_comments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("paragraph_index", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "version_id",
            "paragraph_index",
            name="uq_paragraph_user_comment_key",
        ),
    )
    op.create_index(
        "ix_paragraph_user_comments_doc_ver",
        "paragraph_user_comments",
        ["document_id", "version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_paragraph_user_comments_doc_ver", table_name="paragraph_user_comments")
    op.drop_table("paragraph_user_comments")
