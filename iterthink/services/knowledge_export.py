"""Export paragraph notes, Impact annotations, and Difference check overrides."""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink import checks as checks_mod
from iterthink.db.models import ImpactAnnotation, ParagraphAnalysis, ParagraphUserComment
from iterthink.db.content_models import Content
from iterthink.persistence import content_repo, impact_annotations as impact_ann
from iterthink.persistence import store_db

EXPORT_VERSION = 1


def _lineage_anchor(session: Session, lineage_id: str) -> Content | None:
    return session.execute(
        select(Content)
        .where(Content.lineage_id == lineage_id)
        .where(Content.version_no == 0)
        .limit(1)
    ).scalar_one_or_none()


def version_context(session: Session, content_version_id: int) -> dict[str, Any]:
    ver = content_repo.get_version_row(session, int(content_version_id))
    if ver is None:
        return {"content_version_id": int(content_version_id)}
    anchor = _lineage_anchor(session, ver.lineage_id)
    attrs = content_repo.content_attrs(anchor) if anchor is not None else {}
    path = attrs.get("resolved_path")
    return {
        "content_version_id": int(content_version_id),
        "lineage_id": str(ver.lineage_id),
        "version_no": int(ver.version_no),
        "document_path": str(path) if path else None,
        "document_name": (ver.name or (anchor.name if anchor is not None else None)),
    }


def _serialize_user_comment(row: ParagraphUserComment, *, ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        **ctx,
        "paragraph_index": int(row.paragraph_index),
        "annotation_kind": str(row.annotation_kind or "paragraph"),
        "plan_page_index": int(row.plan_page_index) if row.plan_page_index is not None else None,
        "plan_norm_x": float(row.plan_norm_x) if row.plan_norm_x is not None else None,
        "plan_norm_y": float(row.plan_norm_y) if row.plan_norm_y is not None else None,
        "geometry_json": row.geometry_json,
        "content_hash": row.content_hash,
        "body": row.body or "",
        "created_at": float(row.created_at),
        "updated_at": float(row.updated_at),
    }


def _serialize_impact_row(row: ImpactAnnotation, *, ctx: dict[str, Any]) -> dict[str, Any]:
    details = impact_ann.parse_details_dict(row)
    return {
        **ctx,
        "paragraph_index": int(row.paragraph_index),
        "prompt_id": str(row.prompt_id),
        "status": str(row.status),
        "comment": row.comment or "",
        "effective_comment": impact_ann.effective_comment(row),
        "model_status": impact_ann.model_status(row) if row.overridden else str(row.status),
        "details": details,
        "overridden": bool(row.overridden),
        "override_comment": row.override_comment,
        "created_at": float(row.created_at),
        "updated_at": float(row.updated_at),
    }


def _serialize_check_row(row: ParagraphAnalysis, payload: dict[str, Any]) -> dict[str, Any]:
    check = checks_mod.get_check(str(row.check_id))
    out: dict[str, Any] = {
        "check_id": str(row.check_id),
        "check_label": check.label if check else str(row.check_id),
        "document_path_key": str(row.document_path_key or ""),
        "model": str(row.model or ""),
        "old_sha256": str(row.old_sha256),
        "new_sha256": str(row.new_sha256),
        "created_at": float(row.created_at),
        "overridden": checks_mod.is_overridden(payload),
        "result": payload,
    }
    if checks_mod.is_overridden(payload):
        if check is not None:
            out["effective_symbol"] = checks_mod.effective_symbol(check, payload)
            out["effective_recommendation"] = checks_mod.effective_primary_recommendation(payload)
            out["model_symbol"] = checks_mod.model_symbol(check, payload)
            out["model_summary"] = checks_mod.model_summary(check, payload)
            out["model_recommendation"] = checks_mod.model_primary_recommendation(payload)
        else:
            ov = checks_mod._override_block(payload) or {}
            sym_field = str(row.check_id)
            out["effective_symbol"] = str(ov.get("symbol") or payload.get("symbol") or "")
            out["effective_recommendation"] = str(ov.get("recommendation") or "")
            out["model_symbol"] = str(ov.get("model_symbol") or payload.get(sym_field) or "")
            out["model_summary"] = str(ov.get("model_summary") or "")
            out["model_recommendation"] = str(ov.get("model_recommendation") or "")
    return out


def _serialize_override_embedding(
    row: tuple[Any, ...],
    *,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    (
        content_version_id,
        paragraph_index,
        prompt_id,
        paragraph_text_hash,
        status,
        override_comment,
        embed_text,
        embed_model_id,
        updated_at,
    ) = row
    return {
        **ctx,
        "paragraph_index": int(paragraph_index),
        "prompt_id": str(prompt_id),
        "paragraph_text_hash": str(paragraph_text_hash),
        "status": str(status),
        "override_comment": str(override_comment),
        "embed_text": str(embed_text),
        "embed_model_id": str(embed_model_id),
        "updated_at": float(updated_at),
    }


def build_export_payload(session: Session, *, store_conn: Any) -> dict[str, Any]:
    version_ctx_cache: dict[int, dict[str, Any]] = {}

    def ctx_for(version_id: int) -> dict[str, Any]:
        vid = int(version_id)
        if vid not in version_ctx_cache:
            version_ctx_cache[vid] = version_context(session, vid)
        return version_ctx_cache[vid]

    user_comments = [
        _serialize_user_comment(r, ctx=ctx_for(r.content_version_id))
        for r in session.execute(select(ParagraphUserComment).order_by(
            ParagraphUserComment.content_version_id,
            ParagraphUserComment.paragraph_index,
        )).scalars()
    ]

    impact_rows = [
        _serialize_impact_row(r, ctx=ctx_for(r.content_version_id))
        for r in session.execute(select(ImpactAnnotation).order_by(
            ImpactAnnotation.content_version_id,
            ImpactAnnotation.paragraph_index,
            ImpactAnnotation.prompt_id,
        )).scalars()
    ]

    check_rows: list[dict[str, Any]] = []
    for row in session.execute(select(ParagraphAnalysis).order_by(
        ParagraphAnalysis.check_id,
        ParagraphAnalysis.created_at.desc(),
    )).scalars():
        try:
            payload = json.loads(row.result_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if not checks_mod.is_overridden(payload):
            continue
        check_rows.append(_serialize_check_row(row, payload))

    override_embeddings: list[dict[str, Any]] = []
    if store_conn is not None:
        for row in store_db.impact_override_context_list_all(store_conn):
            override_embeddings.append(
                _serialize_override_embedding(row, ctx=ctx_for(int(row[0])))
            )

    return {
        "export_version": EXPORT_VERSION,
        "exported_at": time.time(),
        "counts": {
            "paragraph_user_comments": len(user_comments),
            "impact_annotations": len(impact_rows),
            "difference_check_overrides": len(check_rows),
            "impact_override_embeddings": len(override_embeddings),
        },
        "paragraph_user_comments": user_comments,
        "impact_annotations": impact_rows,
        "difference_check_overrides": check_rows,
        "impact_override_embeddings": override_embeddings,
    }


def export_json_text(session: Session, *, store_conn: Any) -> str:
    payload = build_export_payload(session, store_conn=store_conn)
    return json.dumps(payload, ensure_ascii=False, indent=2)
