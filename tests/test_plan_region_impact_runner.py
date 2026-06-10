"""Tests for plan region impact orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pypdf import PdfWriter

from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, plan_pdf_annotations
from iterthink.services.plan_change_regions import DetectedChangeRegion
from iterthink.services.plan_region_impact_runner import analyze_plan_change_regions


def _write_blank_pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        w.write(f)


def _persist_plan_pair(md: Path, pdf_a: Path, pdf_b: Path) -> tuple[int, int]:
    with session_scope() as s:
        base_vid = content_repo.persist_version_snapshot(
            s,
            md.resolve(),
            md.read_text(encoding="utf-8"),
            "import",
            skip_if_unchanged_sha=False,
            pdf_source_path=pdf_a,
            pdf_profile="plan",
        )
        cand_vid = content_repo.persist_version_snapshot(
            s,
            md.resolve(),
            md.read_text(encoding="utf-8"),
            "import",
            skip_if_unchanged_sha=False,
            pdf_source_path=pdf_b,
            pdf_profile="plan",
        )
        assert base_vid is not None and cand_vid is not None
        return int(base_vid), int(cand_vid)


@pytest.mark.asyncio
async def test_analyze_plan_change_regions_mocked(
    ephemeral_store: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = tmp_path / "plan.md"
    md.write_text("<!-- pdf_profile:plan -->\n", encoding="utf-8")
    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    _write_blank_pdf(pdf_a)
    _write_blank_pdf(pdf_b)
    base_vid, cand_vid = _persist_plan_pair(md, pdf_a, pdf_b)

    reg = DetectedChangeRegion(
        region_key="rk",
        page_index=0,
        norm_bbox=(0.2, 0.2, 0.4, 0.4),
        pixel_count=500,
    )
    with session_scope() as s:
        anns = plan_pdf_annotations.sync_auto_change_regions(
            s,
            candidate_version_id=cand_vid,
            baseline_version_id=base_vid,
            regions=[reg],
        )
        ann_id = int(anns[0].id)

    ollama = MagicMock()
    monkeypatch.setattr(
        "iterthink.services.plan_region_impact_runner.check_plan_impact_vision_ready",
        AsyncMock(return_value=(True, "Ready")),
    )
    monkeypatch.setattr(
        "iterthink.services.plan_region_impact_runner.assess_plan_region_impact_async",
        AsyncMock(return_value="Spatial shift near corridor."),
    )
    monkeypatch.setattr(
        "iterthink.services.plan_region_impact_runner.embed_image_crop_sync",
        lambda _p: [0.2] * 768,
    )

    with session_scope() as s:
        results = await analyze_plan_change_regions(
            ollama,
            s,
            doc_path=md,
            baseline_version_id=base_vid,
            candidate_version_id=cand_vid,
            region_ids=[ann_id],
        )
    assert len(results) == 1
    assert results[0].region_id == ann_id
    assert "Spatial shift" in results[0].impact_narrative
    assert results[0].embedding_id.isdigit()
