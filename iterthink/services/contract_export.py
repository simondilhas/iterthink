"""Export PBS projection rows as NDJSON (stub for cloud import)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink.db.change_models import ContentChange, SyncOutbox
from iterthink.db.content_models import Content, ContentFileLink, FileRecord


def _row_dict(obj: Any, columns: list[str]) -> dict[str, Any]:
    return {c: getattr(obj, c) for c in columns}


def iter_content_rows(session: Session) -> Iterator[str]:
    for row in session.scalars(select(Content).order_by(Content.id)):
        payload = {
            "table": "content",
            "id": row.id,
            "workspace_id": row.workspace_id,
            "project_id": row.project_id,
            "lineage_id": row.lineage_id,
            "version_no": row.version_no,
            "contract_id": row.contract_id,
            "content_kind": row.content_kind,
            "canonical_type": row.canonical_type,
            "attributes": row.attributes,
        }
        yield json.dumps(payload, ensure_ascii=False)


def iter_content_changes(session: Session) -> Iterator[str]:
    for row in session.scalars(select(ContentChange).order_by(ContentChange.id)):
        payload = {
            "table": "content_changes",
            "id": row.id,
            "content_version_id": row.content_version_id,
            "change_class": row.change_class,
            "property_path": row.property_path,
            "intent_verdict": row.intent_verdict,
        }
        yield json.dumps(payload, ensure_ascii=False)


def export_entity_ndjson(session: Session, out_path: Path) -> int:
    """Write content + changes to *out_path*. Returns line count."""
    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for line in iter_content_rows(session):
            f.write(line + "\n")
            n += 1
        for line in iter_content_changes(session):
            f.write(line + "\n")
            n += 1
    return n


def enqueue_sync_stub(session: Session, *, entity_table: str, entity_id: int, op: str = "upsert") -> None:
    session.add(SyncOutbox(entity_table=entity_table, entity_id=entity_id, op=op))
    session.flush()
