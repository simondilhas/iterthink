"""Plan PDF pins and revision clouds stored in paragraph_user_comments."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from iterthink.db.models import ParagraphUserComment
from iterthink.persistence import content_changes

KIND_PARAGRAPH = "paragraph"
KIND_PIN = "pin"
KIND_REVISION_CLOUD = "revision_cloud"
KIND_CHANGE_REGION = "change_region"

_PLAN_KINDS = (KIND_PIN, KIND_REVISION_CLOUD, KIND_CHANGE_REGION)


@dataclass(frozen=True)
class PlanAnnotation:
    id: int
    content_version_id: int
    paragraph_index: int
    annotation_kind: str
    plan_page_index: int
    plan_norm_x: float | None
    plan_norm_y: float | None
    body: str
    geometry_json: str | None

    @property
    def document_id(self) -> int:
        return self.content_version_id

    @property
    def version_id(self) -> int:
        return self.content_version_id

    def cloud_bbox_norm(self) -> dict[str, float] | None:
        if self.annotation_kind not in (KIND_REVISION_CLOUD, KIND_CHANGE_REGION):
            return None
        return self.region_bbox_norm()

    def region_bbox_norm(self) -> dict[str, float] | None:
        if not self.geometry_json:
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

    def region_meta(self) -> dict:
        if not self.geometry_json:
            return {}
        try:
            data = json.loads(self.geometry_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}


def _row_to_annotation(row: ParagraphUserComment) -> PlanAnnotation:
    return PlanAnnotation(
        id=int(row.id),
        content_version_id=int(row.content_version_id),
        paragraph_index=int(row.paragraph_index),
        annotation_kind=str(row.annotation_kind or KIND_PIN),
        plan_page_index=int(row.plan_page_index or 0),
        plan_norm_x=float(row.plan_norm_x) if row.plan_norm_x is not None else None,
        plan_norm_y=float(row.plan_norm_y) if row.plan_norm_y is not None else None,
        body=(row.body or "").strip(),
        geometry_json=row.geometry_json,
    )


def next_paragraph_slot(session: Session, *, content_version_id: int) -> int:
    session.flush()
    indices = session.execute(
        select(ParagraphUserComment.paragraph_index).where(
            ParagraphUserComment.content_version_id == int(content_version_id),
        )
    ).scalars()
    slots = [int(i) for i in indices]
    if not slots:
        return 0
    return max(slots) + 1


def list_for_plan_version(session: Session, *, content_version_id: int) -> list[PlanAnnotation]:
    rows = session.execute(
        select(ParagraphUserComment)
        .where(
            ParagraphUserComment.content_version_id == int(content_version_id),
            ParagraphUserComment.annotation_kind.in_(_PLAN_KINDS),
        )
        .order_by(ParagraphUserComment.plan_page_index, ParagraphUserComment.id)
    ).scalars()
    return [_row_to_annotation(r) for r in rows]


def list_pins_and_clouds_by_page(
    session: Session, *, content_version_id: int, page_index: int
) -> list[PlanAnnotation]:
    return [
        a
        for a in list_for_plan_version(session, content_version_id=content_version_id)
        if a.plan_page_index == int(page_index)
    ]


def get_by_id(session: Session, annotation_id: int) -> PlanAnnotation | None:
    row = session.get(ParagraphUserComment, int(annotation_id))
    if row is None or row.annotation_kind not in _PLAN_KINDS:
        return None
    return _row_to_annotation(row)


def get_by_paragraph_index(
    session: Session, *, content_version_id: int, paragraph_index: int
) -> PlanAnnotation | None:
    row = (
        session.execute(
            select(ParagraphUserComment).where(
                ParagraphUserComment.content_version_id == int(content_version_id),
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
    content_version_id: int,
    plan_page_index: int,
    plan_norm_x: float,
    plan_norm_y: float,
    body: str = "",
) -> PlanAnnotation:
    now = time.time()
    slot = next_paragraph_slot(session, content_version_id=content_version_id)
    row = ParagraphUserComment(
        content_version_id=int(content_version_id),
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
    ann = _row_to_annotation(row)
    content_changes.sync_plan_annotation_geometry(session, ann)
    return ann


def insert_revision_cloud(
    session: Session,
    *,
    content_version_id: int,
    plan_page_index: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    body: str = "",
) -> PlanAnnotation:
    now = time.time()
    slot = next_paragraph_slot(session, content_version_id=content_version_id)
    lo_x, hi_x = sorted((float(x0), float(x1)))
    lo_y, hi_y = sorted((float(y0), float(y1)))
    geom = json.dumps({"x0": lo_x, "y0": lo_y, "x1": hi_x, "y1": hi_y})
    cx = (lo_x + hi_x) * 0.5
    cy = (lo_y + hi_y) * 0.5
    row = ParagraphUserComment(
        content_version_id=int(content_version_id),
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
    ann = _row_to_annotation(row)
    content_changes.sync_plan_annotation_geometry(session, ann)
    return ann


def update_body(session: Session, *, annotation_id: int, body: str) -> None:
    row = session.get(ParagraphUserComment, int(annotation_id))
    if row is None:
        return
    row.body = (body or "").strip()
    row.updated_at = time.time()


def delete_annotation(session: Session, *, annotation_id: int) -> None:
    row = session.get(ParagraphUserComment, int(annotation_id))
    if row is not None:
        content_changes.delete_plan_annotation_geometry(
            session,
            content_version_id=int(row.content_version_id),
            annotation_id=int(annotation_id),
        )
    session.execute(delete(ParagraphUserComment).where(ParagraphUserComment.id == int(annotation_id)))


def plan_comments_map_for_ki(session: Session, *, content_version_id: int) -> dict[int, str]:
    out: dict[int, str] = {}
    for a in list_for_plan_version(session, content_version_id=content_version_id):
        text = (a.body or "").strip()
        if a.annotation_kind == KIND_CHANGE_REGION or text:
            out[a.paragraph_index] = text
    return out


def list_change_regions_for_version(
    session: Session, *, content_version_id: int
) -> list[PlanAnnotation]:
    return [
        a
        for a in list_for_plan_version(session, content_version_id=content_version_id)
        if a.annotation_kind == KIND_CHANGE_REGION
    ]


def _norm_bbox_tuple(data: dict) -> tuple[float, float, float, float] | None:
    keys = ("x0", "y0", "x1", "y1")
    if not all(k in data for k in keys):
        return None
    return (float(data["x0"]), float(data["y0"]), float(data["x1"]), float(data["y1"]))


def _bbox_iou_norm(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 1e-9 else 0.0


def sync_auto_change_regions(
    session: Session,
    *,
    candidate_version_id: int,
    baseline_version_id: int,
    regions: list,
) -> list[PlanAnnotation]:
    """
    Upsert auto-detected change regions on the candidate version.
    Preserves paragraph_index + body when region_key or IoU matches.
    """
    from iterthink.services.plan_change_regions import DetectedChangeRegion

    existing = list_change_regions_for_version(
        session, content_version_id=int(candidate_version_id)
    )
    # Drop auto regions from a different baseline pair
    for ann in existing:
        meta = ann.region_meta()
        if int(meta.get("baseline_version_id") or 0) != int(baseline_version_id):
            delete_annotation(session, annotation_id=int(ann.id))
    session.flush()
    existing = list_change_regions_for_version(
        session, content_version_id=int(candidate_version_id)
    )
    by_key = {a.region_meta().get("region_key"): a for a in existing if a.region_meta().get("region_key")}

    out: list[PlanAnnotation] = []
    now = time.time()
    for reg in regions:
        if not isinstance(reg, DetectedChangeRegion):
            continue
        rk = reg.region_key
        nb = reg.norm_bbox
        match: PlanAnnotation | None = by_key.get(rk)
        if match is None:
            for ann in existing:
                if ann.id in {a.id for a in out}:
                    continue
                old_nb = _norm_bbox_tuple(ann.region_meta())
                if old_nb and _bbox_iou_norm(old_nb, nb) >= 0.5:
                    match = ann
                    break
        geom = {
            "x0": nb[0],
            "y0": nb[1],
            "x1": nb[2],
            "y1": nb[3],
            "baseline_version_id": int(baseline_version_id),
            "region_key": rk,
            "source": "auto",
            "dismissed": match.region_meta().get("dismissed", False) if match else False,
            "reviewed": match.region_meta().get("reviewed", False) if match else False,
            "pixel_count": int(reg.pixel_count),
            "text_change_ids": list(reg.text_change_ids),
        }
        cx = (nb[0] + nb[2]) * 0.5
        cy = (nb[1] + nb[3]) * 0.5
        if match is not None:
            row = session.get(ParagraphUserComment, int(match.id))
            if row is not None:
                row.geometry_json = json.dumps(geom)
                row.plan_page_index = int(reg.page_index)
                row.plan_norm_x = cx
                row.plan_norm_y = cy
                row.updated_at = now
                session.flush()
                ann = _row_to_annotation(row)
                content_changes.sync_plan_annotation_geometry(session, ann)
                out.append(ann)
                continue
        from iterthink.studio.ki_comments import change_region_placeholder_body

        slot = next_paragraph_slot(session, content_version_id=int(candidate_version_id))
        row = ParagraphUserComment(
            content_version_id=int(candidate_version_id),
            paragraph_index=slot,
            annotation_kind=KIND_CHANGE_REGION,
            plan_page_index=int(reg.page_index),
            plan_norm_x=cx,
            plan_norm_y=cy,
            geometry_json=json.dumps(geom),
            content_hash=None,
            body=change_region_placeholder_body(int(reg.page_index)),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        ann = _row_to_annotation(row)
        content_changes.sync_plan_annotation_geometry(session, ann)
        out.append(ann)

    # Remove stale auto regions not matched in this detection pass
    detected_keys = {r.region_key for r in regions if isinstance(r, DetectedChangeRegion)}
    for ann in list_change_regions_for_version(
        session, content_version_id=int(candidate_version_id)
    ):
        meta = ann.region_meta()
        if meta.get("source") != "auto":
            continue
        rk = meta.get("region_key")
        if rk not in detected_keys and ann.id not in {a.id for a in out}:
            nb = _norm_bbox_tuple(meta)
            if nb is None:
                delete_annotation(session, annotation_id=int(ann.id))
                continue
            matched = any(
                isinstance(r, DetectedChangeRegion)
                and _bbox_iou_norm(nb, r.norm_bbox) >= 0.5
                for r in regions
            )
            if not matched:
                delete_annotation(session, annotation_id=int(ann.id))

    session.flush()
    return list_change_regions_for_version(
        session, content_version_id=int(candidate_version_id)
    )


def update_change_region_flags(
    session: Session,
    *,
    annotation_id: int,
    dismissed: bool | None = None,
    reviewed: bool | None = None,
) -> PlanAnnotation | None:
    ann = get_by_id(session, int(annotation_id))
    if ann is None or ann.annotation_kind != KIND_CHANGE_REGION:
        return None
    row = session.get(ParagraphUserComment, int(annotation_id))
    if row is None:
        return None
    meta = ann.region_meta()
    if dismissed is not None:
        meta["dismissed"] = bool(dismissed)
    if reviewed is not None:
        meta["reviewed"] = bool(reviewed)
    row.geometry_json = json.dumps(meta)
    row.updated_at = time.time()
    session.flush()
    updated = _row_to_annotation(row)
    content_changes.sync_plan_annotation_geometry(session, updated)
    return updated


def annotations_to_region_views(annotations: list[PlanAnnotation]) -> list:
    from iterthink.services.plan_change_regions import PlanChangeRegionView

    views: list[PlanChangeRegionView] = []
    for a in annotations:
        if a.annotation_kind != KIND_CHANGE_REGION:
            continue
        bbox = a.region_bbox_norm()
        if bbox is None:
            continue
        meta = a.region_meta()
        nb = (bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
        views.append(
            PlanChangeRegionView(
                region_id=int(a.id),
                page_index=int(a.plan_page_index),
                norm_bbox=nb,
                paragraph_index=int(a.paragraph_index),
                body=(a.body or "").strip(),
                pixel_count=int(meta.get("pixel_count") or 0),
                text_change_ids=tuple(meta.get("text_change_ids") or ()),
                dismissed=bool(meta.get("dismissed")),
                reviewed=bool(meta.get("reviewed")),
                region_key=str(meta.get("region_key") or ""),
            )
        )
    return views


def plan_comment_list_title(ann: PlanAnnotation) -> str:
    if ann.annotation_kind == KIND_PIN:
        label = "pin"
    elif ann.annotation_kind == KIND_CHANGE_REGION:
        label = "area"
    else:
        label = "cloud"
    return f"Page {ann.plan_page_index + 1} · {label}"


def cloud_bbox_from_points(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
    lo_x, hi_x = sorted((float(x0), float(x1)))
    lo_y, hi_y = sorted((float(y0), float(y1)))
    return {"x0": lo_x, "y0": lo_y, "x1": hi_x, "y1": hi_y}
