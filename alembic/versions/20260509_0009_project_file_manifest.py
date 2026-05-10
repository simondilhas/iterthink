"""project_file_manifest for impact RAG tracking

Revision ID: 20260509_0009
Revises: 20260508_0008
Create Date: 2026-05-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260509_0009"
down_revision: Union[str, None] = "20260508_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("project_file_manifest"):
        return

    op.create_table(
        "project_file_manifest",
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("embed_model_id", sa.Text(), nullable=False),
        sa.Column("file_mtime", sa.Float(), nullable=False),
        sa.Column("chunk_hashes", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("file_path", "embed_model_id"),
    )


def downgrade() -> None:
    op.drop_table("project_file_manifest")
