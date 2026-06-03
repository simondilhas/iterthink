"""Plan PDF pins and revision clouds stored in paragraph_user_comments."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from iterthink.db.models import ParagraphUserComment

KIND_PARAGRAPH = "paragraph"
KIND_PIN = "pin"
KIND_REVISION_CLOUD = "revision_cloud"

_PLAN_KINDS = (KIND_PIN, KIND_REVISION_CLOUD)


@dataclass(frozen=True)
class PlanAnnotation:
    id: int
    document_id: int
    version_id: int
    paragraph_index: int
    annotation_kind: str
    plan_page_index: int
    plan_norm_x: float | None
    plan_norm_y: float | None
    body: str
    geometry_json: str | None

    def cloud_bbox_norm(self) -> dict[str, float] | None:
        if self.annotation_kind != KIND_REVISION_CLOUD or not self.geometry_json:
            return None
        try:
            data = json.loads(self.geometry_json)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        keys = ("x0", "y0", "x1", "y1")
        if not all(k in data for k in keys):
            return None
        return {k: float(data[k]) for k in keys}


def _row_to_annotation(row: ParagraphUserComment) -> PlanAnnotation:
    return PlanAnnotation(
        id=int(row.id),
        document_id=int(row.document_id),
        version_id=int(row.version_id),
        paragraph_index=int(row.paragraph_index),
        annotation_kind=str(row.annotation_kind or KIND_PIN),
        plan_page_index=int(row.plan_page_index or 0),
        plan_norm_x=float(row.plan_norm_x) if row.plan_norm_x is not None else None,
        plan_norm_y=float(row.plan_norm_y) if row.plan_norm_y is not None else None,
        body=(row.body or "").strip(),
        geometry_json=row.geometry_json,
    )


def next_paragraph_slot(session: Session, *, document_id: int, version_id: int) -> int:
    session.flush()
    indices = session.execute(
        select(ParagraphUserComment.paragraph_index).where(
            ParagraphUserComment.document_id == int(document_id),
            ParagraphUserComment.version_id == int(version_id),
        )
    ).scalars()
    slots = [int(i) for i in indices]
    if not slots:
        return 0
    return max(slots) + 1


def list_for_plan_version(
    session: Session, *, document_id: int, version_id: int
) -> list[PlanAnnotation]:
    rows = session.execute(
        select(ParagraphUserComment)
        .where(
            ParagraphUserComment.document_id == int(document_id),
            ParagraphUserComment.version_id == int(version_id),
            ParagraphUserComment.annotation_kind.in_(_PLAN_KINDS),
        )
        .order_by(ParagraphUserComment.plan_page_index, ParagraphUserComment.id)
    ).scalars()
    return [_row_to_annotation(r) for r in rows]


def list_pins_and_clouds_by_page(
    session: Session, *, document_id: int, version_id: int, page_index: int
) -> list[PlanAnnotation]:
    return [
        a
        for a in list_for_plan_version(session, document_id=document_id, version_id=version_id)
        if a.plan_page_index == int(page_index)
    ]


def get_by_id(session: Session, annotation_id: int) -> PlanAnnotation | None:
    row = session.get(ParagraphUserComment, int(annotation_id))
    if row is None or row.annotation_kind not in _PLAN_KINDS:
        return None
    return _row_to_annotation(row)


def get_by_paragraph_index(
    session: Session, *, document_id: int, version_id: int, paragraph_index: int
) -> PlanAnnotation | None:
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.document_id == int(document_id),
                ParagraphUserComment.version_id == int(version_id),
                ParagraphUserComment.paragraph_index == int(paragraph_index),
                ParagraphUserComment.annotation_kind.in_(_PLAN_KINDS),
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    return _row_to_annotation(row)


def insert_pin(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    plan_page_index: int,
    plan_norm_x: float,
    plan_norm_y: float,
    body: str = "",
) -> PlanAnnotation:
    now = time.time()
    slot = next_paragraph_slot(session, document_id=document_id, version_id=version_id)
    row = ParagraphUserComment(
        document_id=int(document_id),
        version_id=int(version_id),
        paragraph_index=slot,
        annotation_kind=KIND_PIN,
        plan_page_index=int(plan_page_index),
        plan_norm_x=float(plan_norm_x),
        plan_norm_y=float(plan_norm_y),
        geometry_json=None,
        content_hash=None,
        body=(body or "").strip(),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return _row_to_annotation(row)


def insert_revision_cloud(
    session: Session,
    *,
    document_id: int,
    version_id: int,
    plan_page_index: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    body: str = "",
) -> PlanAnnotation:
    now = time.time()
    slot = next_paragraph_slot(session, document_id=document_id, version_id=version_id)
    lo_x, hi_x = sorted((float(x0), float(x1)))
    lo_y, hi_y = sorted((float(y0), float(y1)))
    geom = json.dumps({"x0": lo_x, "y0": lo_y, "x1": hi_x, "y1": hi_y})
    cx = (lo_x + hi_x) * 0.5
    cy = (lo_y + hi_y) * 0.5
    row = ParagraphUserComment(
        document_id=int(document_id),
        version_id=int(version_id),
        paragraph_index=slot,
        annotation_kind=KIND_REVISION_CLOUD,
        plan_page_index=int(plan_page_index),
        plan_norm_x=cx,
        plan_norm_y=cy,
        geometry_json=geom,
        content_hash=None,
        body=(body or "").strip(),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return _row_to_annotation(row)


def update_body(session: Session, *, annotation_id: int, body: str) -> None:
    row = session.get(ParagraphUserComment, int(annotation_id))
    if row is None:
        return
    row.body = (body or "").strip()
    row.updated_at = time.time()


def delete_annotation(session: Session, *, annotation_id: int) -> None:
    session.execute(delete(ParagraphUserComment).where(ParagraphUserComment.id == int(annotation_id)))


def plan_comments_map_for_ki(
    session: Session, *, document_id: int, version_id: int
) -> dict[int, str]:
    """Paragraph-index keyed comment bodies for KI list (user text only)."""
    out: dict[int, str] = {}
    for a in list_for_plan_version(session, document_id=document_id, version_id=version_id):
        text = (a.body or "").strip()
        if text:
            out[a.paragraph_index] = text
    return out


def plan_comment_list_title(ann: PlanAnnotation) -> str:
    label = "pin" if ann.annotation_kind == KIND_PIN else "cloud"
    return f"Page {ann.plan_page_index + 1} · {label}"


def cloud_bbox_from_points(
    x0: float, y0: float, x1: float, y1: float
) -> dict[str, float]:
    lo_x, hi_x = sorted((float(x0), float(x1)))
    lo_y, hi_y = sorted((float(y0), float(y1)))
    return {"x0": lo_x, "y0": lo_y, "x1": hi_x, "y1": hi_y}
