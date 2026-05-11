"""credential_vault for encrypted API keys

Revision ID: 20260506_0007
Revises: 20260505_0006
Create Date: 2026-05-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260506_0007"
down_revision: Union[str, None] = "20260505_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credential_vault",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("kdf_salt", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("verifier", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("credential_vault")
