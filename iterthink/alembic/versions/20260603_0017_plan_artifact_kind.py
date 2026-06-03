"""Backfill lineage artifact_kind=plan from legacy version pdf_profile.

Revision ID: 20260603_0017
Revises: 20260603_0016
Create Date: 2026-06-03
"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260603_0017"
down_revision: Union[str, None] = "20260603_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ARTIFACT_KIND_PLAN = "plan"


def _parse_attrs(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("content"):
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, lineage_id, version_no, attributes FROM content ORDER BY lineage_id, version_no"
        )
    ).fetchall()

    anchors: dict[str, tuple[int, dict[str, Any]]] = {}
    plan_lineages: set[str] = set()

    for row in rows:
        lid = str(row.lineage_id)
        attrs = _parse_attrs(row.attributes)
        if int(row.version_no) == 0:
            anchors[lid] = (int(row.id), attrs)
        elif str(attrs.get("pdf_profile") or "").strip() == "plan":
            plan_lineages.add(lid)

    for lid in plan_lineages:
        anchor = anchors.get(lid)
        if anchor is None:
            continue
        anchor_id, attrs = anchor
        if str(attrs.get("artifact_kind") or "").strip() == _ARTIFACT_KIND_PLAN:
            continue
        attrs["artifact_kind"] = _ARTIFACT_KIND_PLAN
        bind.execute(
            sa.text("UPDATE content SET attributes = :attrs WHERE id = :id"),
            {"attrs": json.dumps(attrs, ensure_ascii=False), "id": anchor_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("content"):
        return

    rows = bind.execute(
        sa.text("SELECT id, attributes FROM content WHERE version_no = 0")
    ).fetchall()
    for row in rows:
        attrs = _parse_attrs(row.attributes)
        if str(attrs.get("artifact_kind") or "").strip() != _ARTIFACT_KIND_PLAN:
            continue
        attrs["artifact_kind"] = "text_document"
        bind.execute(
            sa.text("UPDATE content SET attributes = :attrs WHERE id = :id"),
            {"attrs": json.dumps(attrs, ensure_ascii=False), "id": int(row.id)},
        )
