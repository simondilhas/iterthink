"""Scope paragraph_analysis cache rows to a document path key.

Revision ID: 20260511_0012
Revises: 20260510_0011
Create Date: 2026-05-11

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260511_0012"
down_revision: Union[str, None] = "20260510_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("paragraph_analysis")}
    uq_names = {u["name"] for u in inspector.get_unique_constraints("paragraph_analysis")}
    if "uq_paragraph_analysis_path_key" in uq_names:
        return
    with op.batch_alter_table("paragraph_analysis") as batch:
        if "document_path_key" not in cols:
            batch.add_column(
                sa.Column(
                    "document_path_key",
                    sa.String(length=64),
                    nullable=False,
                    server_default="",
                ),
            )
        if "uq_paragraph_analysis_key" in uq_names:
            batch.drop_constraint("uq_paragraph_analysis_key", type_="unique")
        batch.create_unique_constraint(
            "uq_paragraph_analysis_path_key",
            ["check_id", "old_sha256", "new_sha256", "model", "document_path_key"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    uq_names = {u["name"] for u in inspector.get_unique_constraints("paragraph_analysis")}
    cols = {c["name"] for c in inspector.get_columns("paragraph_analysis")}
    if "document_path_key" not in cols:
        return
    with op.batch_alter_table("paragraph_analysis") as batch:
        if "uq_paragraph_analysis_path_key" in uq_names:
            batch.drop_constraint("uq_paragraph_analysis_path_key", type_="unique")
        batch.create_unique_constraint(
            "uq_paragraph_analysis_key",
            ["check_id", "old_sha256", "new_sha256", "model"],
        )
        batch.drop_column("document_path_key")
