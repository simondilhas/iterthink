"""Tests for plan PDF annotation CRUD."""

from __future__ import annotations

from pathlib import Path

from iterthink.db.session import session_scope
from iterthink.persistence import plan_pdf_annotations, paragraph_user_comments, version_storage


def _persist_doc(tmp_path: Path) -> tuple[int, int]:
    md = tmp_path / "note.md"
    md.write_text("A\n\nB", encoding="utf-8")
    with session_scope() as s:
        vid = version_storage.persist_version_snapshot(s, md.resolve(), "A\n\nB", "manual")
        assert vid is not None
        doc = version_storage.get_document_by_resolved_path(s, md.resolve())
        assert doc is not None
        return int(doc.id), int(vid)


def test_insert_pin_and_list(ephemeral_store: None, tmp_path: Path) -> None:
    doc_id, vid = _persist_doc(tmp_path)
    with session_scope() as s:
        ann = plan_pdf_annotations.insert_pin(
            s,
            document_id=doc_id,
            version_id=vid,
            plan_page_index=0,
            plan_norm_x=0.25,
            plan_norm_y=0.75,
            body="door note",
        )
        listed = plan_pdf_annotations.list_for_plan_version(
            s, document_id=doc_id, version_id=vid
        )
    assert len(listed) == 1
    assert listed[0].id == ann.id
    assert listed[0].annotation_kind == plan_pdf_annotations.KIND_PIN
    assert listed[0].plan_page_index == 0
    assert listed[0].body == "door note"


def test_multiple_pins_same_page(ephemeral_store: None, tmp_path: Path) -> None:
    doc_id, vid = _persist_doc(tmp_path)
    with session_scope() as s:
        plan_pdf_annotations.insert_pin(
            s,
            document_id=doc_id,
            version_id=vid,
            plan_page_index=1,
            plan_norm_x=0.1,
            plan_norm_y=0.2,
        )
        plan_pdf_annotations.insert_pin(
            s,
            document_id=doc_id,
            version_id=vid,
            plan_page_index=1,
            plan_norm_x=0.5,
            plan_norm_y=0.5,
        )
        listed = plan_pdf_annotations.list_for_plan_version(
            s, document_id=doc_id, version_id=vid
        )
    assert len(listed) == 2
    assert listed[0].paragraph_index != listed[1].paragraph_index


def test_revision_cloud_bbox(ephemeral_store: None, tmp_path: Path) -> None:
    doc_id, vid = _persist_doc(tmp_path)
    with session_scope() as s:
        ann = plan_pdf_annotations.insert_revision_cloud(
            s,
            document_id=doc_id,
            version_id=vid,
            plan_page_index=2,
            x0=0.6,
            y0=0.1,
            x1=0.3,
            y1=0.4,
        )
    bbox = ann.cloud_bbox_norm()
    assert bbox is not None
    assert bbox["x0"] == 0.3
    assert bbox["x1"] == 0.6


def test_paragraph_upsert_still_unique(ephemeral_store: None, tmp_path: Path) -> None:
    doc_id, vid = _persist_doc(tmp_path)
    with session_scope() as s:
        paragraph_user_comments.upsert(
            s,
            document_id=doc_id,
            version_id=vid,
            paragraph_index=0,
            body="md comment",
            paragraph_body="A\n\nB",
        )
        stored = paragraph_user_comments.list_stored_for_version(
            s, document_id=doc_id, version_id=vid
        )
    assert len(stored) == 1
    assert stored[0].body == "md comment"
