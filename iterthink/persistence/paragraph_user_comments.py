"""CRUD and version migration for user paragraph comments."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

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


@dataclass(frozen=True)
class StoredAnalyseComment:
    paragraph_index: int
    check_id: str
    body: str
    symbol: str | None = None
    details_json: str | None = None
    content_hash: str | None = None
    overridden: bool = False


@dataclass(frozen=True)
class ResolvedAnalyseComment:
    display_paragraph_index: int
    check_id: str
    body: str
    symbol: str | None
    payload: dict[str, Any] | None
    overridden: bool = False


def alignment_old_to_new_paragraph_index(old_text: str, new_text: str) -> dict[int, int]:
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


def _resolve_single_paragraph_index(
    *,
    stored_index: int,
    content_hash: str | None,
    hash_map: dict[str, list[int]],
    idx_map: dict[int, int],
) -> int | None:
    target: int | None = None
    h = (content_hash or "").strip()
    if h and h in hash_map:
        candidates = hash_map[h]
        if len(candidates) == 1:
            target = candidates[0]
        else:
            target = min(candidates, key=lambda i: abs(i - stored_index))
    if target is None:
        mapped = idx_map.get(int(stored_index))
        if mapped is not None:
            target = int(mapped)
    return target


def resolve_analyse_for_body(
    anchor_body: str,
    display_body: str,
    stored: Iterable[StoredAnalyseComment],
) -> list[ResolvedAnalyseComment]:
    rows = [
        StoredAnalyseComment(
            paragraph_index=int(c.paragraph_index),
            check_id=str(c.check_id),
            body=(c.body or "").strip(),
            symbol=c.symbol,
            details_json=c.details_json,
            content_hash=(c.content_hash or None),
            overridden=bool(c.overridden),
        )
        for c in stored
        if (c.body or "").strip() or (c.details_json or "").strip()
    ]
    if not rows:
        return []

    hash_map = _display_hash_to_indices(display_body)
    idx_map = alignment_old_to_new_paragraph_index(anchor_body, display_body)
    out: list[ResolvedAnalyseComment] = []
    orphans: list[StoredAnalyseComment] = []

    for c in rows:
        target = _resolve_single_paragraph_index(
            stored_index=int(c.paragraph_index),
            content_hash=c.content_hash,
            hash_map=hash_map,
            idx_map=idx_map,
        )
        if target is None:
            orphans.append(c)
            continue
        payload: dict[str, Any] | None = None
        raw = (c.details_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = None
        out.append(
            ResolvedAnalyseComment(
                display_paragraph_index=int(target),
                check_id=c.check_id,
                body=c.body,
                symbol=c.symbol,
                payload=payload,
                overridden=bool(c.overridden),
            )
        )

    for c in orphans:
        payload: dict[str, Any] | None = None
        raw = (c.details_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = None
        out.append(
            ResolvedAnalyseComment(
                display_paragraph_index=0,
                check_id=c.check_id,
                body=c.body,
                symbol=c.symbol,
                payload=payload,
                overridden=bool(c.overridden),
            )
        )
    return out


def list_analyse_for_version(
    session: Session,
    *,
    content_version_id: int,
    check_id: str | None = None,
) -> list[StoredAnalyseComment]:
    q = select(ParagraphUserComment).where(
        ParagraphUserComment.content_version_id == content_version_id,
        ParagraphUserComment.annotation_kind == "analyse",
    )
    if check_id is not None:
        q = q.where(ParagraphUserComment.source_id == str(check_id))
    rows = session.execute(q).scalars()
    return [
        StoredAnalyseComment(
            paragraph_index=int(r.paragraph_index),
            check_id=str(r.source_id or ""),
            body=r.body or "",
            symbol=r.symbol,
            details_json=r.details_json,
            content_hash=r.content_hash,
            overridden=bool(r.overridden),
        )
        for r in rows
        if (r.source_id or "").strip()
    ]


def list_analyse_check_ids_for_version(
    session: Session, *, content_version_id: int
) -> list[str]:
    rows = session.execute(
        select(ParagraphUserComment.source_id).where(
            ParagraphUserComment.content_version_id == content_version_id,
            ParagraphUserComment.annotation_kind == "analyse",
            ParagraphUserComment.source_id.is_not(None),
        ).distinct()
    ).all()
    return sorted({str(r[0]) for r in rows if r[0]})


def _parse_analyse_compare_version_pair(
    payload: dict[str, Any],
) -> tuple[int | None, int | None] | None:
    meta = payload.get("_iterthink_compare_version_pair")
    if not isinstance(meta, dict):
        return None
    base_raw = meta.get("baseline_version_id")
    cand_raw = meta.get("candidate_version_id")
    base_id: int | None
    cand_id: int | None
    if base_raw is None:
        base_id = None
    else:
        try:
            base_id = int(base_raw)
        except (TypeError, ValueError):
            return None
    if cand_raw is None:
        cand_id = None
    else:
        try:
            cand_id = int(cand_raw)
        except (TypeError, ValueError):
            return None
    return base_id, cand_id


def analyse_compare_version_pair_from_stored(
    session: Session,
    *,
    content_version_id: int,
    check_id: str,
) -> tuple[int | None, int | None] | None:
    rows = list_analyse_for_version(
        session, content_version_id=content_version_id, check_id=check_id
    )
    for row in rows:
        raw = (row.details_json or "").strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        pair = _parse_analyse_compare_version_pair(parsed)
        if pair is not None:
            return pair
    return None


def map_analyse_resolved_for_display(
    session: Session,
    *,
    content_version_id: int,
    anchor_body: str,
    display_body: str,
    check_id: str | None = None,
) -> list[ResolvedAnalyseComment]:
    stored = list_analyse_for_version(
        session, content_version_id=content_version_id, check_id=check_id
    )
    return resolve_analyse_for_body(anchor_body, display_body, stored)


def get_analyse_payload(
    session: Session,
    *,
    content_version_id: int,
    check_id: str,
    paragraph_index: int,
) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.content_version_id == content_version_id,
                ParagraphUserComment.paragraph_index == int(paragraph_index),
                ParagraphUserComment.annotation_kind == "analyse",
                ParagraphUserComment.source_id == str(check_id),
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    raw = (row.details_json or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def get_analyse_payload_resolved(
    session: Session,
    *,
    content_version_id: int,
    anchor_body: str,
    display_body: str,
    check_id: str,
    paragraph_index: int,
) -> dict[str, Any] | None:
    for row in map_analyse_resolved_for_display(
        session,
        content_version_id=content_version_id,
        anchor_body=anchor_body,
        display_body=display_body,
        check_id=check_id,
    ):
        if int(row.display_paragraph_index) == int(paragraph_index):
            return row.payload
    return None


def upsert_analyse(
    session: Session,
    *,
    content_version_id: int,
    paragraph_index: int,
    check_id: str,
    body: str,
    symbol: str | None,
    payload: dict[str, Any],
    content_hash: str | None = None,
    candidate_paragraph: str | None = None,
    overridden: bool = False,
    override_comment: str | None = None,
) -> None:
    now = time.time()
    body = (body or "").strip()
    check_id = str(check_id)
    if content_hash is None and candidate_paragraph is not None:
        content_hash = compute_hash(candidate_paragraph)
    details_json = json.dumps(payload)
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.content_version_id == content_version_id,
                ParagraphUserComment.paragraph_index == int(paragraph_index),
                ParagraphUserComment.annotation_kind == "analyse",
                ParagraphUserComment.source_id == check_id,
            )
        )
        .scalars()
        .first()
    )
    if not body and not (payload or {}):
        if row is not None:
            session.delete(row)
        return
    if row is None:
        session.add(
            ParagraphUserComment(
                content_version_id=content_version_id,
                paragraph_index=int(paragraph_index),
                annotation_kind="analyse",
                source_id=check_id,
                symbol=symbol,
                details_json=details_json,
                content_hash=content_hash,
                body=body or "(analysis result)",
                overridden=bool(overridden),
                override_comment=override_comment,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        row.body = body or row.body
        row.symbol = symbol
        row.details_json = details_json
        row.content_hash = content_hash
        row.overridden = bool(overridden)
        row.override_comment = override_comment
        row.updated_at = now
    session.flush()


def delete_analyse_at(
    session: Session,
    *,
    content_version_id: int,
    paragraph_index: int,
    check_id: str,
) -> None:
    session.execute(
        delete(ParagraphUserComment).where(
            ParagraphUserComment.content_version_id == content_version_id,
            ParagraphUserComment.paragraph_index == int(paragraph_index),
            ParagraphUserComment.annotation_kind == "analyse",
            ParagraphUserComment.source_id == str(check_id),
        )
    )


def list_stored_for_version(session: Session, *, content_version_id: int) -> list[StoredComment]:
    rows = session.execute(
        select(ParagraphUserComment).where(
            ParagraphUserComment.content_version_id == content_version_id,
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


def map_for_version(session: Session, *, content_version_id: int) -> dict[int, str]:
    rows = session.execute(
        select(ParagraphUserComment).where(
            ParagraphUserComment.content_version_id == content_version_id,
            ParagraphUserComment.annotation_kind == "paragraph",
        )
    ).scalars()
    return {int(r.paragraph_index): (r.body or "").strip() for r in rows if (r.body or "").strip()}


def map_resolved_for_display(
    session: Session,
    *,
    content_version_id: int,
    anchor_body: str,
    display_body: str,
) -> dict[int, str]:
    stored = list_stored_for_version(session, content_version_id=content_version_id)
    return resolve_comments_for_body(anchor_body, display_body, stored)


def get_one(session: Session, *, content_version_id: int, paragraph_index: int) -> str | None:
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.content_version_id == content_version_id,
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
    content_version_id: int,
    anchor_body: str,
    display_body: str,
    paragraph_index: int,
) -> str | None:
    resolved = map_resolved_for_display(
        session,
        content_version_id=content_version_id,
        anchor_body=anchor_body,
        display_body=display_body,
    )
    text = (resolved.get(int(paragraph_index)) or "").strip()
    return text if text else None


def upsert(
    session: Session,
    *,
    content_version_id: int,
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
                ParagraphUserComment.content_version_id == content_version_id,
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
                content_version_id=content_version_id,
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


def delete_at(session: Session, *, content_version_id: int, paragraph_index: int) -> None:
    session.execute(
        delete(ParagraphUserComment).where(
            ParagraphUserComment.content_version_id == content_version_id,
            ParagraphUserComment.paragraph_index == int(paragraph_index),
            ParagraphUserComment.annotation_kind == "paragraph",
        )
    )


def migrate_comments_to_new_version(
    session: Session,
    *,
    parent_version_id: int,
    new_version_id: int,
    old_body: str,
    new_body: str,
) -> None:
    stored = list_stored_for_version(session, content_version_id=parent_version_id)
    if not stored:
        return
    resolved = resolve_comments_for_body(old_body, new_body, stored)
    for new_i, text in resolved.items():
        upsert(
            session,
            content_version_id=new_version_id,
            paragraph_index=int(new_i),
            body=text,
            paragraph_body=new_body,
        )
    session.flush()


def merge_with_impact_for_export(
    impact_by_idx: dict[int, str],
    user_by_idx: dict[int, str],
) -> dict[int, str]:
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
