"""Persist PBS PropertyChange rows and plan geometries from studio actions."""

from __future__ import annotations

import json
import time
from typing import Literal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from iterthink.contract import enums as pbs
from iterthink.contract.paths import paragraph_property_path
from iterthink.db.change_models import ContentChange
from iterthink.db.content_models import Content, ContentGeometry

SemanticKind = Literal["STABLE", "NEW"]

GEOMETRY_ROLE_PLAN_PIN = "plan_pin"
GEOMETRY_ROLE_PLAN_CLOUD = "plan_revision_cloud"
GEOMETRY_SOURCE_ANNOTATION_PREFIX = "annotation:"


def record_paragraph_semantic_change(
    session: Session,
    *,
    content_version_id: int,
    paragraph_index: int,
    old_text: str,
    new_text: str,
    kind: SemanticKind,
    from_revision: int | None = None,
    to_revision: int | None = None,
) -> None:
    row = session.get(Content, content_version_id)
    if row is None:
        return
    fr = from_revision if from_revision is not None else max(0, int(row.version_no) - 1)
    tr = to_revision if to_revision is not None else int(row.version_no)
    verdict = pbs.INTENT_VERDICT_NEW if kind == "NEW" else pbs.INTENT_VERDICT_STABLE
    session.add(
        ContentChange(
            content_version_id=content_version_id,
            lineage_id=row.lineage_id,
            change_class=pbs.CHANGE_CLASS_PROPERTY,
            change_type=pbs.CHANGE_TYPE_PROPERTY,
            from_revision=fr,
            to_revision=tr,
            affected_subject_id=str(content_version_id),
            affected_subject_type=pbs.CANONICAL_TYPE_ARTIFACT,
            property_path=paragraph_property_path(paragraph_index),
            property_path_kind=pbs.PROPERTY_PATH_KIND_DOCUMENT,
            from_value=old_text,
            to_value=new_text,
            intent_verdict=verdict,
            detected_at=time.time(),
            change_source="iterthink.paragraph_semantics",
        )
    )
    session.flush()


def record_paragraph_batch(
    session: Session,
    *,
    content_version_id: int,
    pairs: list[tuple[int, str, str, SemanticKind]],
    from_revision: int | None = None,
    to_revision: int | None = None,
) -> None:
    for idx, old_t, new_t, kind in pairs:
        record_paragraph_semantic_change(
            session,
            content_version_id=content_version_id,
            paragraph_index=idx,
            old_text=old_t,
            new_text=new_t,
            kind=kind,
            from_revision=from_revision,
            to_revision=to_revision,
        )


def record_semantic_compare_batch(
    session: Session,
    *,
    newer_content_version_id: int,
    baseline_content_version_id: int | None,
    pairs: list[tuple[int, str, str, SemanticKind]],
) -> None:
    newer = session.get(Content, int(newer_content_version_id))
    if newer is None:
        return
    baseline = (
        session.get(Content, int(baseline_content_version_id))
        if baseline_content_version_id is not None
        else None
    )
    fr = int(baseline.version_no) if baseline is not None else max(0, int(newer.version_no) - 1)
    tr = int(newer.version_no)
    record_paragraph_batch(
        session,
        content_version_id=int(newer_content_version_id),
        pairs=pairs,
        from_revision=fr,
        to_revision=tr,
    )


def _geometry_source_for_annotation(annotation_id: int) -> str:
    return f"{GEOMETRY_SOURCE_ANNOTATION_PREFIX}{int(annotation_id)}"


def sync_plan_annotation_geometry(session: Session, ann: Any) -> None:
    """Mirror a plan pin/cloud row into ``content_geometries`` (PBS GeometryChange target)."""
    from iterthink.persistence.plan_pdf_annotations import (
        KIND_CHANGE_REGION,
        KIND_PIN,
        KIND_REVISION_CLOUD,
        PlanAnnotation,
    )

    if not isinstance(ann, PlanAnnotation):
        return
    row = session.get(Content, int(ann.content_version_id))
    if row is None:
        return
    if ann.annotation_kind == KIND_PIN:
        role = GEOMETRY_ROLE_PLAN_PIN
    elif ann.annotation_kind in (KIND_REVISION_CLOUD, KIND_CHANGE_REGION):
        role = GEOMETRY_ROLE_PLAN_CLOUD
    else:
        return
    src = _geometry_source_for_annotation(ann.id)
    session.execute(
        delete(ContentGeometry).where(
            ContentGeometry.content_id == int(ann.content_version_id),
            ContentGeometry.geometry_source == src,
        )
    )
    if ann.annotation_kind in (KIND_REVISION_CLOUD, KIND_CHANGE_REGION):
        geom = ann.geometry_json or json.dumps(ann.region_bbox_norm() or {})
    else:
        geom = json.dumps(
            {
                "type": "Point",
                "coordinates": [
                    float(ann.plan_norm_x or 0.5),
                    float(ann.plan_norm_y or 0.5),
                ],
            }
        )
    payload = json.dumps(
        {
            "annotation_id": int(ann.id),
            "annotation_kind": ann.annotation_kind,
            "plan_page_index": int(ann.plan_page_index),
            "paragraph_index": int(ann.paragraph_index),
            "body": (ann.body or "").strip(),
        }
    )
    now = time.time()
    session.add(
        ContentGeometry(
            workspace_id=int(row.workspace_id),
            project_id=int(row.project_id),
            content_id=int(ann.content_version_id),
            geometry_role=role,
            geometry_source=src,
            geometry_space="plan_norm",
            geom=geom,
            payload=payload,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def delete_plan_annotation_geometry(
    session: Session, *, content_version_id: int, annotation_id: int
) -> None:
    src = _geometry_source_for_annotation(annotation_id)
    session.execute(
        delete(ContentGeometry).where(
            ContentGeometry.content_id == int(content_version_id),
            ContentGeometry.geometry_source == src,
        )
    )


def enrich_plan_region_impact_geometry(
    session: Session,
    *,
    annotation_id: int,
    impact_narrative: str,
    vec_rowid: int,
    chunk_id: str,
) -> None:
    """Merge impact analysis fields into mirrored ``content_geometries.payload``."""
    from sqlalchemy import select

    from iterthink.persistence.plan_pdf_annotations import get_by_id

    ann = get_by_id(session, int(annotation_id))
    if ann is None:
        return
    src = _geometry_source_for_annotation(int(annotation_id))
    geom_row = session.execute(
        select(ContentGeometry).where(
            ContentGeometry.content_id == int(ann.content_version_id),
            ContentGeometry.geometry_source == src,
        )
    ).scalar_one_or_none()
    if geom_row is None:
        sync_plan_annotation_geometry(session, ann)
        geom_row = session.execute(
            select(ContentGeometry).where(
                ContentGeometry.content_id == int(ann.content_version_id),
                ContentGeometry.geometry_source == src,
            )
        ).scalar_one_or_none()
    if geom_row is None:
        return
    try:
        payload = json.loads(geom_row.payload or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["impact_narrative"] = (impact_narrative or "").strip()
    payload["vec_rowid"] = int(vec_rowid)
    payload["chunk_id"] = str(chunk_id)
    geom_row.payload = json.dumps(payload)
    geom_row.updated_at = time.time()
    session.flush()
