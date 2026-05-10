"""CRUD for ImpactAnnotation rows."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink.db.models import ImpactAnnotation


def parse_details_dict(row: ImpactAnnotation) -> dict | None:
    raw = getattr(row, "details_json", None)
    if not raw or not str(raw).strip():
        return None
    try:
        obj = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def format_details_for_export(details: dict | None) -> str:
    if not details:
        return ""
    chunks: list[str] = []
    findings = details.get("findings")
    if isinstance(findings, list) and findings:
        fb: list[str] = []
        for fi, item in enumerate(findings):
            if not isinstance(item, dict):
                continue
            fb.append(f"Finding {fi + 1}:")
            for k, v in item.items():
                if v is None or (isinstance(v, str) and not v.strip()):
                    continue
                fb.append(f"  {k}: {v}")
        if fb:
            chunks.append("\n".join(fb))
    nar = details.get("not_applicable_reason")
    if isinstance(nar, str) and nar.strip():
        chunks.append(f"Not applicable: {nar.strip()}")
    if details.get("low_confidence") is True:
        chunks.append("Low confidence: context may be incomplete for this paragraph.")
    ex = details.get("explanation")
    if isinstance(ex, str) and ex.strip():
        chunks.append(ex.strip())
    refs = details.get("references")
    if isinstance(refs, list) and refs:
        lines: list[str] = []
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            doc = ref.get("document")
            para = ref.get("paragraph")
            note = ref.get("note")
            if isinstance(doc, str) and doc.strip():
                line = f"- {doc.strip()}"
                if isinstance(para, int):
                    line += f" (paragraph {para})"
                elif isinstance(para, float) and int(para) == para:
                    line += f" (paragraph {int(para)})"
                if isinstance(note, str) and note.strip():
                    line += f": {note.strip()}"
                lines.append(line)
        if lines:
            chunks.append("References:\n" + "\n".join(lines))
    return "\n\n".join(chunks) if chunks else ""


def upsert_model_result(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    paragraph_index: int,
    prompt_id: str,
    status: str,
    comment: str,
    details: dict | None = None,
) -> None:
    now = time.time()
    row = (
        session.execute(
            select(ImpactAnnotation).where(
                ImpactAnnotation.document_id == document_id,
                ImpactAnnotation.version_id == version_id,
                ImpactAnnotation.paragraph_index == paragraph_index,
                ImpactAnnotation.prompt_id == prompt_id,
            )
        )
        .scalars()
        .first()
    )
    body = json.dumps(details, ensure_ascii=False) if isinstance(details, dict) else None
    if row is None:
        session.add(
            ImpactAnnotation(
                document_id=document_id,
                version_id=version_id,
                paragraph_index=paragraph_index,
                prompt_id=prompt_id,
                status=status,
                comment=comment,
                details_json=body,
                overridden=False,
                override_comment=None,
                created_at=now,
                updated_at=now,
            )
        )
        return
    row.status = status
    row.comment = comment
    row.details_json = body
    row.updated_at = now


def set_override(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    paragraph_index: int,
    prompt_id: str,
    override_comment: str,
) -> None:
    now = time.time()
    row = (
        session.execute(
            select(ImpactAnnotation).where(
                ImpactAnnotation.document_id == document_id,
                ImpactAnnotation.version_id == version_id,
                ImpactAnnotation.paragraph_index == paragraph_index,
                ImpactAnnotation.prompt_id == prompt_id,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        session.add(
            ImpactAnnotation(
                document_id=document_id,
                version_id=version_id,
                paragraph_index=paragraph_index,
                prompt_id=prompt_id,
                status="stable",
                comment="",
                details_json=None,
                overridden=True,
                override_comment=override_comment,
                created_at=now,
                updated_at=now,
            )
        )
        return
    row.overridden = True
    row.override_comment = override_comment
    row.updated_at = now


def list_for_version(
    session: Session, *, document_id: int, version_id: int, prompt_id: str
) -> dict[int, ImpactAnnotation]:
    rows = session.execute(
        select(ImpactAnnotation).where(
            ImpactAnnotation.document_id == document_id,
            ImpactAnnotation.version_id == version_id,
            ImpactAnnotation.prompt_id == prompt_id,
        )
    ).scalars()
    return {r.paragraph_index: r for r in rows}


def paragraph_comments_map_for_export(
    session: Session, *, document_id: int, version_id: int
) -> dict[int, str]:
    """Merge annotations for Word export: index → combined text (multiple prompts)."""
    rows = list(
        session.execute(
            select(ImpactAnnotation)
            .where(
                ImpactAnnotation.document_id == document_id,
                ImpactAnnotation.version_id == version_id,
            )
            .order_by(ImpactAnnotation.paragraph_index, ImpactAnnotation.prompt_id)
        ).scalars()
    )
    by_idx: dict[int, list[str]] = {}
    for r in rows:
        t = effective_comment(r).strip()
        extra = format_details_for_export(parse_details_dict(r))
        if extra:
            block = f"{t}\n\n{extra}".strip() if t else extra
        else:
            block = t
        if not block:
            continue
        by_idx.setdefault(int(r.paragraph_index), []).append(f"{r.prompt_id}: {block}")
    return {i: "\n".join(parts) for i, parts in sorted(by_idx.items())}


def list_all_prompts_for_version(
    session: Session, *, document_id: int, version_id: int
) -> list[ImpactAnnotation]:
    return list(
        session.execute(
            select(ImpactAnnotation).where(
                ImpactAnnotation.document_id == document_id,
                ImpactAnnotation.version_id == version_id,
            ).order_by(ImpactAnnotation.paragraph_index, ImpactAnnotation.prompt_id)
        ).scalars()
    )


def effective_comment(row: ImpactAnnotation) -> str:
    if row.overridden and (row.override_comment or "").strip():
        return (row.override_comment or "").strip()
    return (row.comment or "").strip()


def snapshot_row_ui(row: ImpactAnnotation) -> dict[str, Any]:
    """Copy ORM fields while *row* is session-bound; safe to use after the session closes."""
    return {
        "status": str(row.status),
        "effective_comment": effective_comment(row),
        "details": parse_details_dict(row),
        "document_id": int(row.document_id),
        "version_id": int(row.version_id),
        "paragraph_index": int(row.paragraph_index),
        "prompt_id": str(row.prompt_id),
    }
