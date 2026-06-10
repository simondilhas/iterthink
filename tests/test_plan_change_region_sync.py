"""Tests for plan change-region sync orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, plan_pdf_annotations
from iterthink.services.plan_change_region_sync import sync_detected_change_regions
from iterthink.services.plan_change_regions import DetectedChangeRegion


def _write_blank_pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        w.write(f)


def _persist_plan_version(md: Path, pdf: Path) -> int:
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(
            s,
            md.resolve(),
            md.read_text(encoding="utf-8"),
            "import",
            skip_if_unchanged_sha=False,
            pdf_source_path=pdf,
            pdf_profile="plan",
        )
        assert vid is not None
        return int(vid)


def test_sync_detected_change_regions_persists_regions(
    ephemeral_store: None, tmp_path: Path
) -> None:
    md = tmp_path / "plan.md"
    md.write_text("<!-- pdf_profile:plan -->\n", encoding="utf-8")
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    _write_blank_pdf(pdf_a)
    _write_blank_pdf(pdf_b)
    base_vid = _persist_plan_version(md, pdf_a)
    cand_vid = _persist_plan_version(md, pdf_b)

    fake = [
        DetectedChangeRegion(
            region_key="rk1",
            page_index=0,
            norm_bbox=(0.1, 0.2, 0.3, 0.4),
            pixel_count=400,
        )
    ]

    with patch(
        "iterthink.services.plan_change_region_sync.detect_change_regions",
        return_value=fake,
    ):
        with session_scope() as s:
            anns = sync_detected_change_regions(
                s,
                doc_path=md,
                baseline_version_id=base_vid,
                candidate_version_id=cand_vid,
            )

    assert len(anns) == 1
    assert anns[0].annotation_kind == plan_pdf_annotations.KIND_CHANGE_REGION
    assert anns[0].body == "Changed area · Page 1"
    assert int(anns[0].region_meta().get("baseline_version_id") or 0) == base_vid


def test_sync_detected_change_regions_same_version_returns_existing(
    ephemeral_store: None, tmp_path: Path
) -> None:
    from iterthink.services.plan_change_regions import DetectedChangeRegion

    vid = tmp_path / "note.md"
    vid.write_text("x", encoding="utf-8")
    with session_scope() as s:
        v = content_repo.persist_version_snapshot(s, vid.resolve(), "x", "manual")
        assert v is not None
        reg = DetectedChangeRegion(
            region_key="k",
            page_index=0,
            norm_bbox=(0.0, 0.0, 0.1, 0.1),
            pixel_count=500,
        )
        plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=int(v),
            baseline_version_id=1,
            regions=[reg],
        )
        listed = sync_detected_change_regions(
            s,
            doc_path=vid,
            baseline_version_id=int(v),
            candidate_version_id=int(v),
        )
    assert len(listed) == 1
