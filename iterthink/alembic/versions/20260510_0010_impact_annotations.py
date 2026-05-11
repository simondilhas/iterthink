"""impact_annotations for Review Impact tab

Revision ID: 20260510_0010
Revises: 20260509_0009
Create Date: 2026-05-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260510_0010"
down_revision: Union[str, None] = "20260509_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("impact_annotations"):
        return

    op.create_table(
        "impact_annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("paragraph_index", sa.Integer(), nullable=False),
        sa.Column("prompt_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("overridden", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("override_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["document_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "version_id",
            "paragraph_index",
            "prompt_id",
            name="uq_impact_annotation_key",
        ),
    )
    op.create_index(
        "ix_impact_annotations_doc_ver_prompt",
        "impact_annotations",
        ["document_id", "version_id", "prompt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_impact_annotations_doc_ver_prompt", table_name="impact_annotations")
    op.drop_table("impact_annotations")
