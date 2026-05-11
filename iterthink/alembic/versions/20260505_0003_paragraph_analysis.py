"""paragraph_analysis cache table

Revision ID: 20260505_0003
Revises: 20260505_0002
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260505_0003"
down_revision: Union[str, None] = "20260505_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paragraph_analysis",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("check_id", sa.String(length=64), nullable=False),
        sa.Column("old_sha256", sa.String(length=64), nullable=False),
        sa.Column("new_sha256", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "check_id", "old_sha256", "new_sha256", "model",
            name="uq_paragraph_analysis_key",
        ),
    )
    op.create_index(
        "ix_paragraph_analysis_check_id",
        "paragraph_analysis",
        ["check_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_paragraph_analysis_check_id", table_name="paragraph_analysis")
    op.drop_table("paragraph_analysis")
