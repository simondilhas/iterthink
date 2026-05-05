"""document_versions display_label

Revision ID: 20260505_0002
Revises: 20260204_0001
Create Date: 2026-05-05

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260505_0002"
down_revision: Union[str, None] = "20260204_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("display_label", sa.String(length=256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "display_label")
