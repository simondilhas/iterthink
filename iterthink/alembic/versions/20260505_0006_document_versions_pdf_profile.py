"""document_versions pdf_profile

Revision ID: 20260505_0006
Revises: 20260505_0005
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260505_0006"
down_revision: Union[str, None] = "20260505_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("pdf_profile", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "pdf_profile")
