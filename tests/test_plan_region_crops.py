"""Tests for plan region crop helpers."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from iterthink.services.plan_region_crops import (
    build_region_crop_set,
    expand_norm_bbox_centered,
)
from iterthink.services.plan_region_context import crop_norm_bbox_from_page_png


def test_expand_norm_bbox_centered_doubles_and_clamps() -> None:
    nb = (0.4, 0.4, 0.6, 0.6)
    out = expand_norm_bbox_centered(nb, scale=2.0)
    assert out[0] < nb[0]
    assert out[1] < nb[1]
    assert out[2] > nb[2]
    assert out[3] > nb[3]
    assert out[0] >= 0.0 and out[3] <= 1.0


def test_build_region_crop_set_writes_files(tmp_path: Path) -> None:
    md = tmp_path / "plan.md"
    md.write_text("x", encoding="utf-8")
    base_png = tmp_path / "base.png"
    cand_png = tmp_path / "cand.png"
    Image.new("RGB", (200, 200), color=(10, 10, 10)).save(base_png)
    Image.new("RGB", (200, 200), color=(20, 20, 20)).save(cand_png)
    nb = (0.25, 0.25, 0.75, 0.75)
    crops = build_region_crop_set(
        doc_path=md,
        candidate_version_id=42,
        region_key="rk1",
        base_page_png=base_png,
        cand_page_png=cand_png,
        norm_bbox=nb,
    )
    assert crops.crop_before.is_file()
    assert crops.crop_after.is_file()
    assert crops.context_crop.is_file()
    assert len(crop_norm_bbox_from_page_png(cand_png, nb)) > 0
