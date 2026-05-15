"""CRUD and version migration for user paragraph comments."""

from __future__ import annotations

import time
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from iterthink.compare.paragraph_align import compute_alignment
from iterthink.db.models import ParagraphUserComment


def alignment_old_to_new_paragraph_index(old_text: str, new_text: str) -> dict[int, int]:
    """Map parent snapshot paragraph indices to new-body indices (best-effort)."""
    diffs = compute_alignment(old_text, new_text)
    out: dict[int, int] = {}
    for d in diffs:
        if d.old_index >= 0 and d.new_index >= 0:
            out[int(d.old_index)] = int(d.new_index)
    return out


def map_for_version(session: Session, *, document_id: int, version_id: int) -> dict[int, str]:
    rows = session.execute(
        select(ParagraphUserComment).where(
            ParagraphUserComment.document_id == document_id,
            ParagraphUserComment.version_id == version_id,
        )
    ).scalars()
    return {int(r.paragraph_index): (r.body or "").strip() for r in rows if (r.body or "").strip()}


def get_one(session: Session, *, document_id: int, version_id: int, paragraph_index: int) -> str | None:
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.document_id == document_id,
                ParagraphUserComment.version_id == version_id,
                ParagraphUserComment.paragraph_index == int(paragraph_index),
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    b = (row.body or "").strip()
    return b if b else None


def upsert(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    paragraph_index: int,
    body: str,
) -> None:
    now = time.time()
    body = (body or "").strip()
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.document_id == document_id,
                ParagraphUserComment.version_id == version_id,
                ParagraphUserComment.paragraph_index == int(paragraph_index),
            )
        )
        .scalars()
        .first()
    )
    if not body:
        if row is not None:
            session.delete(row)
        return
    if row is None:
        session.add(
            ParagraphUserComment(
                document_id=document_id,
                version_id=version_id,
                paragraph_index=int(paragraph_index),
                body=body,
                created_at=now,
                updated_at=now,
            )
        )
        return
    row.body = body
    row.updated_at = now


def delete_at(session: Session, *, document_id: int, version_id: int, paragraph_index: int) -> None:
    session.execute(
        delete(ParagraphUserComment).where(
            ParagraphUserComment.document_id == document_id,
            ParagraphUserComment.version_id == version_id,
            ParagraphUserComment.paragraph_index == int(paragraph_index),
        )
    )


def migrate_comments_to_new_version(
    session: Session,
    *,
    document_id: int,
    parent_version_id: int,
    new_version_id: int,
    old_body: str,
    new_body: str,
) -> None:
    """Copy user comments from parent snapshot to ``new_version_id`` using paragraph alignment."""
    from collections import defaultdict

    old_map = map_for_version(session, document_id=document_id, version_id=parent_version_id)
    if not old_map:
        return
    idx_map = alignment_old_to_new_paragraph_index(old_body, new_body)
    bucket: defaultdict[int, list[str]] = defaultdict(list)
    for old_i, text in old_map.items():
        new_i = idx_map.get(int(old_i))
        if new_i is None:
            continue
        t = (text or "").strip()
        if t:
            bucket[int(new_i)].append(t)
    for new_i, texts in bucket.items():
        merged = "\n---\n".join(dict.fromkeys(texts))
        upsert(
            session,
            document_id=document_id,
            version_id=new_version_id,
            paragraph_index=int(new_i),
            body=merged,
        )


def merge_with_impact_for_export(
    impact_by_idx: dict[int, str],
    user_by_idx: dict[int, str],
) -> dict[int, str]:
    """Combine Impact export strings with user notes for Word comment export."""
    keys = sorted(set(impact_by_idx) | set(user_by_idx))
    out: dict[int, str] = {}
    for i in keys:
        chunks: list[str] = []
        u = (user_by_idx.get(i) or "").strip()
        im = (impact_by_idx.get(i) or "").strip()
        if u:
            chunks.append(f"Note: {u}")
        if im:
            chunks.append(im)
        if chunks:
            out[i] = "\n\n".join(chunks)
    return out
