"""Tests for plan PDF annotation CRUD."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from iterthink.db.content_models import ContentGeometry
from iterthink.db.session import session_scope
from iterthink.persistence import content_changes, content_repo, plan_pdf_annotations, paragraph_user_comments


def _persist_doc(tmp_path: Path) -> int:
    md = tmp_path / "note.md"
    md.write_text("A\n\nB", encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), "A\n\nB", "manual")
        assert vid is not None
        return int(vid)


def test_insert_pin_and_list(ephemeral_store: None, tmp_path: Path) -> None:
    vid = _persist_doc(tmp_path)
    with session_scope() as s:
        ann = plan_pdf_annotations.insert_pin(
            s,
            content_version_id=vid,
            plan_page_index=0,
            plan_norm_x=0.25,
            plan_norm_y=0.75,
            body="door note",
        )
        listed = plan_pdf_annotations.list_for_plan_version(s, content_version_id=vid)
        geoms = s.scalars(
            select(ContentGeometry).where(ContentGeometry.content_id == vid)
        ).all()
        assert len(geoms) == 1
        assert geoms[0].geometry_role == content_changes.GEOMETRY_ROLE_PLAN_PIN
    assert len(listed) == 1
    assert listed[0].id == ann.id
    assert listed[0].annotation_kind == plan_pdf_annotations.KIND_PIN
    assert listed[0].plan_page_index == 0


def test_multiple_pins_same_page(ephemeral_store: None, tmp_path: Path) -> None:
    vid = _persist_doc(tmp_path)
    with session_scope() as s:
        plan_pdf_annotations.insert_pin(
            s,
            content_version_id=vid,
            plan_page_index=1,
            plan_norm_x=0.1,
            plan_norm_y=0.2,
        )
        plan_pdf_annotations.insert_pin(
            s,
            content_version_id=vid,
            plan_page_index=1,
            plan_norm_x=0.9,
            plan_norm_y=0.8,
        )
        on_page = plan_pdf_annotations.list_pins_and_clouds_by_page(
            s, content_version_id=vid, page_index=1
        )
    assert len(on_page) == 2


def test_revision_cloud_bbox(ephemeral_store: None, tmp_path: Path) -> None:
    vid = _persist_doc(tmp_path)
    with session_scope() as s:
        ann = plan_pdf_annotations.insert_revision_cloud(
            s,
            content_version_id=vid,
            plan_page_index=0,
            x0=0.1,
            y0=0.2,
            x1=0.4,
            y1=0.5,
        )
    bbox = ann.cloud_bbox_norm()
    assert bbox is not None
    assert bbox["x0"] == 0.1
    assert bbox["y1"] == 0.5


def test_paragraph_upsert_still_unique(ephemeral_store: None, tmp_path: Path) -> None:
    vid = _persist_doc(tmp_path)
    with session_scope() as s:
        paragraph_user_comments.upsert(
            s,
            content_version_id=vid,
            paragraph_index=0,
            body="first",
        )
        paragraph_user_comments.upsert(
            s,
            content_version_id=vid,
            paragraph_index=0,
            body="second",
        )
        m = paragraph_user_comments.map_for_version(s, content_version_id=vid)
    assert m[0] == "second"


def test_sync_auto_change_regions_upsert(ephemeral_store: None, tmp_path: Path) -> None:
    from iterthink.services.plan_change_regions import DetectedChangeRegion

    vid = _persist_doc(tmp_path)
    reg = DetectedChangeRegion(
        region_key="abc123",
        page_index=0,
        norm_bbox=(0.1, 0.2, 0.4, 0.5),
        pixel_count=500,
    )
    with session_scope() as s:
        plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=vid - 1,
            regions=[reg],
        )
        listed = plan_pdf_annotations.list_change_regions_for_version(
            s, content_version_id=vid
        )
    assert len(listed) == 1
    assert listed[0].annotation_kind == plan_pdf_annotations.KIND_CHANGE_REGION
    assert listed[0].region_bbox_norm() is not None
    assert listed[0].body == "Changed area · Page 1"

    reg2 = DetectedChangeRegion(
        region_key="abc123",
        page_index=0,
        norm_bbox=(0.1, 0.2, 0.4, 0.5),
        pixel_count=600,
    )
    with session_scope() as s:
        plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=vid - 1,
            regions=[reg2],
        )
        listed2 = plan_pdf_annotations.list_change_regions_for_version(
            s, content_version_id=vid
        )
    assert len(listed2) == 1
    assert listed2[0].id == listed[0].id
    assert int(listed2[0].region_meta().get("pixel_count") or 0) == 600


def test_sync_preserves_body_on_iou_match(ephemeral_store: None, tmp_path: Path) -> None:
    from iterthink.services.plan_change_regions import DetectedChangeRegion

    vid = _persist_doc(tmp_path)
    reg = DetectedChangeRegion(
        region_key="key1",
        page_index=0,
        norm_bbox=(0.1, 0.2, 0.4, 0.5),
        pixel_count=500,
    )
    with session_scope() as s:
        anns = plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=1,
            regions=[reg],
        )
        plan_pdf_annotations.update_body(
            s, annotation_id=int(anns[0].id), body="user note"
        )
    shifted = DetectedChangeRegion(
        region_key="key2",
        page_index=0,
        norm_bbox=(0.11, 0.21, 0.41, 0.51),
        pixel_count=520,
    )
    with session_scope() as s:
        anns2 = plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=1,
            regions=[shifted],
        )
    assert len(anns2) == 1
    assert anns2[0].body == "user note"
