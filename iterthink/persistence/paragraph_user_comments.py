"""CRUD and version migration for user paragraph comments."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from iterthink.compare.margin import split_paragraphs
from iterthink.compare.paragraph_align import compute_alignment, compute_hash
from iterthink.db.models import ParagraphUserComment


@dataclass(frozen=True)
class StoredComment:
    paragraph_index: int
    body: str
    content_hash: str | None = None


def alignment_old_to_new_paragraph_index(old_text: str, new_text: str) -> dict[int, int]:
    """Map parent snapshot paragraph indices to new-body indices (best-effort)."""
    diffs = compute_alignment(old_text, new_text)
    out: dict[int, int] = {}
    for d in diffs:
        if d.old_index >= 0 and d.new_index >= 0:
            out[int(d.old_index)] = int(d.new_index)
    return out


def hash_for_paragraph(body: str, index: int) -> str | None:
    paras = split_paragraphs(body)
    if index < 0 or index >= len(paras):
        return None
    return compute_hash(paras[index])


def _display_hash_to_indices(display_body: str) -> dict[str, list[int]]:
    paras = split_paragraphs(display_body)
    out: dict[str, list[int]] = defaultdict(list)
    for i, para in enumerate(paras):
        out[compute_hash(para)].append(i)
    return dict(out)


def _merge_comment_texts(texts: list[str]) -> str:
    return "\n---\n".join(dict.fromkeys(t for t in texts if (t or "").strip()))


def resolve_comments_for_body(
    anchor_body: str,
    display_body: str,
    stored: Iterable[StoredComment],
) -> dict[int, str]:
    """
    Map stored comments onto ``display_body`` paragraph indices.

    1. Exact ``content_hash`` match on display paragraphs (tie-break by index distance).
    2. Alignment fallback: ``anchor_body`` index → display index.
    3. Orphans merge into paragraph index 0.
    """
    comments = [
        StoredComment(
            paragraph_index=int(c.paragraph_index),
            body=(c.body or "").strip(),
            content_hash=(c.content_hash or None),
        )
        for c in stored
        if (c.body or "").strip()
    ]
    if not comments:
        return {}

    hash_map = _display_hash_to_indices(display_body)
    idx_map = alignment_old_to_new_paragraph_index(anchor_body, display_body)
    bucket: defaultdict[int, list[str]] = defaultdict(list)
    orphans: list[str] = []

    for c in comments:
        target: int | None = None
        h = (c.content_hash or "").strip()
        if h and h in hash_map:
            candidates = hash_map[h]
            if len(candidates) == 1:
                target = candidates[0]
            else:
                target = min(candidates, key=lambda i: abs(i - c.paragraph_index))

        if target is None:
            mapped = idx_map.get(int(c.paragraph_index))
            if mapped is not None:
                target = int(mapped)

        if target is None:
            orphans.append(c.body)
        else:
            bucket[int(target)].append(c.body)

    if orphans:
        bucket[0].extend(orphans)

    out: dict[int, str] = {}
    for idx, texts in bucket.items():
        merged = _merge_comment_texts(texts)
        if merged:
            out[int(idx)] = merged
    return out


def list_stored_for_version(session: Session, *, document_id: int, version_id: int) -> list[StoredComment]:
    rows = session.execute(
        select(ParagraphUserComment).where(
            ParagraphUserComment.document_id == document_id,
            ParagraphUserComment.version_id == version_id,
            ParagraphUserComment.annotation_kind == "paragraph",
        )
    ).scalars()
    return [
        StoredComment(
            paragraph_index=int(r.paragraph_index),
            body=r.body or "",
            content_hash=r.content_hash,
        )
        for r in rows
    ]


def map_for_version(session: Session, *, document_id: int, version_id: int) -> dict[int, str]:
    rows = session.execute(
        select(ParagraphUserComment).where(
            ParagraphUserComment.document_id == document_id,
            ParagraphUserComment.version_id == version_id,
            ParagraphUserComment.annotation_kind == "paragraph",
        )
    ).scalars()
    return {int(r.paragraph_index): (r.body or "").strip() for r in rows if (r.body or "").strip()}


def map_resolved_for_display(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    anchor_body: str,
    display_body: str,
) -> dict[int, str]:
    stored = list_stored_for_version(session, document_id=document_id, version_id=version_id)
    return resolve_comments_for_body(anchor_body, display_body, stored)


def get_one(session: Session, *, document_id: int, version_id: int, paragraph_index: int) -> str | None:
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.document_id == document_id,
                ParagraphUserComment.version_id == version_id,
                ParagraphUserComment.paragraph_index == int(paragraph_index),
                ParagraphUserComment.annotation_kind == "paragraph",
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    b = (row.body or "").strip()
    return b if b else None


def get_resolved_one(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    anchor_body: str,
    display_body: str,
    paragraph_index: int,
) -> str | None:
    resolved = map_resolved_for_display(
        session,
        document_id=document_id,
        version_id=version_id,
        anchor_body=anchor_body,
        display_body=display_body,
    )
    text = (resolved.get(int(paragraph_index)) or "").strip()
    return text if text else None


def upsert(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    paragraph_index: int,
    body: str,
    content_hash: str | None = None,
    paragraph_body: str | None = None,
) -> None:
    now = time.time()
    body = (body or "").strip()
    if content_hash is None and paragraph_body is not None:
        content_hash = hash_for_paragraph(paragraph_body, int(paragraph_index))
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.document_id == document_id,
                ParagraphUserComment.version_id == version_id,
                ParagraphUserComment.paragraph_index == int(paragraph_index),
                ParagraphUserComment.annotation_kind == "paragraph",
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
                annotation_kind="paragraph",
                content_hash=content_hash,
                body=body,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        row.body = body
        row.content_hash = content_hash
        row.annotation_kind = "paragraph"
        row.updated_at = now
    session.flush()


def delete_at(session: Session, *, document_id: int, version_id: int, paragraph_index: int) -> None:
    session.execute(
        delete(ParagraphUserComment).where(
            ParagraphUserComment.document_id == document_id,
            ParagraphUserComment.version_id == version_id,
            ParagraphUserComment.paragraph_index == int(paragraph_index),
            ParagraphUserComment.annotation_kind == "paragraph",
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
    """Copy user comments from parent snapshot to ``new_version_id`` using hash + alignment."""
    stored = list_stored_for_version(session, document_id=document_id, version_id=parent_version_id)
    if not stored:
        return
    resolved = resolve_comments_for_body(old_body, new_body, stored)
    for new_i, text in resolved.items():
        upsert(
            session,
            document_id=document_id,
            version_id=new_version_id,
            paragraph_index=int(new_i),
            body=text,
            paragraph_body=new_body,
        )
    session.flush()


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
