"""Tests for plan region impact context helpers."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, plan_pdf_annotations
from iterthink.services.plan_change_regions import DetectedChangeRegion
from iterthink.services.plan_region_context import (
    crop_norm_bbox_from_page_png,
    list_region_contexts,
)


def _persist_doc(tmp_path: Path) -> int:
    md = tmp_path / "note.md"
    md.write_text("A\n\nB", encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), "A\n\nB", "manual")
        assert vid is not None
        return int(vid)


def test_list_region_contexts_skips_dismissed(ephemeral_store: None, tmp_path: Path) -> None:
    vid = _persist_doc(tmp_path)
    reg = DetectedChangeRegion(
        region_key="rk",
        page_index=1,
        norm_bbox=(0.1, 0.2, 0.4, 0.5),
        pixel_count=500,
        text_change_ids=("t1",),
    )
    with session_scope() as s:
        anns = plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=1,
            regions=[reg],
        )
        plan_pdf_annotations.update_change_region_flags(
            s, annotation_id=int(anns[0].id), dismissed=True
        )
        active = list_region_contexts(s, candidate_version_id=vid)
    assert active == []


def test_list_region_contexts_maps_fields(ephemeral_store: None, tmp_path: Path) -> None:
    vid = _persist_doc(tmp_path)
    reg = DetectedChangeRegion(
        region_key="rk2",
        page_index=2,
        norm_bbox=(0.2, 0.3, 0.5, 0.6),
        pixel_count=600,
        text_change_ids=("a", "b"),
    )
    with session_scope() as s:
        plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=vid,
            baseline_version_id=9,
            regions=[reg],
        )
        ctxs = list_region_contexts(s, candidate_version_id=vid)
    assert len(ctxs) == 1
    ctx = ctxs[0]
    assert ctx.page_index == 2
    assert ctx.norm_bbox == (0.2, 0.3, 0.5, 0.6)
    assert ctx.baseline_version_id == 9
    assert ctx.candidate_version_id == vid
    assert ctx.text_change_ids == ("a", "b")
    assert ctx.body == "Changed area · Page 3"


def test_crop_norm_bbox_from_page_png(tmp_path: Path) -> None:
    from io import BytesIO

    png = tmp_path / "page.png"
    Image.new("RGB", (100, 100), color=(255, 0, 0)).save(png)
    data = crop_norm_bbox_from_page_png(png, (0.2, 0.2, 0.6, 0.6))
    assert len(data) > 0
    out = Image.open(BytesIO(data))
    assert out.size[0] >= 30
    assert out.size[1] >= 30
