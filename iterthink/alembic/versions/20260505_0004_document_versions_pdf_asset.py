"""document_versions pdf_asset_relpath

Revision ID: 20260505_0004
Revises: 20260505_0003
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260505_0004"
down_revision: Union[str, None] = "20260505_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("pdf_asset_relpath", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "pdf_asset_relpath")
