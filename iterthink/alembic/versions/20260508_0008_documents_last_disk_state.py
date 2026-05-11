"""documents: last_disk_* for external-edit drift detection

Revision ID: 20260508_0008
Revises: 20260506_0007
Create Date: 2026-05-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0008"
down_revision: Union[str, None] = "20260506_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("last_disk_mtime_ns", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("last_disk_size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("last_disk_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("last_disk_sha256")
        batch.drop_column("last_disk_size")
        batch.drop_column("last_disk_mtime_ns")
