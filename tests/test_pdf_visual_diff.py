"""Tests for PDF visual diff overlays."""

from pathlib import Path

import numpy as np
from PIL import Image

from iterthink.tools import pdf_visual_diff as pvd


def _white_bgr(h: int, w: int) -> np.ndarray:
    return np.full((h, w, 3), 255, dtype=np.uint8)


def test_overlay_diff_detects_large_change(tmp_path: Path) -> None:
    pvd._opencv.cache_clear()
    h, w = 120, 120
    a = _white_bgr(h, w)
    b = a.copy()
    b[35:85, 35:85] = (0, 0, 0)
    out = pvd._overlay_diff(a, b, pvd._to_gray(a), pvd._to_gray(b), pdf_profile="plan")
    highlighted = (out[:, :, 1] == 200) | (out[:, :, 2] == 220)
    assert int(np.count_nonzero(highlighted)) >= 400


def test_overlay_diff_detects_thin_stroke_change() -> None:
    pvd._opencv.cache_clear()
    h, w = 200, 200
    ga = np.full((h, w), 255, dtype=np.uint8)
    gb = ga.copy()
    ga[100, 40:120] = 0
    a = np.stack([ga, ga, ga], axis=-1)
    b = np.stack([gb, gb, gb], axis=-1)
    out = pvd._overlay_diff(a, b, ga, gb, pdf_profile="plan")
    highlighted = (out[:, :, 1] == 200) | (out[:, :, 2] == 220)
    assert int(np.count_nonzero(highlighted)) >= 40


def test_plan_cc_filter_keeps_elongated_drops_specks() -> None:
    pvd._opencv.cache_clear()
    cv = pvd._opencv()
    mask = np.zeros((100, 100), dtype=np.uint8)
    cv.rectangle(mask, (10, 10), (14, 14), 255, -1)
    cv.rectangle(mask, (20, 50), (90, 52), 255, -1)
    out = pvd._filter_mask_by_cc_plan(mask)
    n, _, stats, _ = cv.connectedComponentsWithStats(out, connectivity=8)
    areas = [stats[i, cv.CC_STAT_AREA] for i in range(1, n)]
    assert len(areas) == 1
    assert areas[0] >= 60


def test_cc_filter_drops_small_components() -> None:
    pvd._opencv.cache_clear()
    cv = pvd._opencv()
    mask = np.zeros((100, 100), dtype=np.uint8)
    cv.rectangle(mask, (10, 10), (14, 14), 255, -1)
    cv.rectangle(mask, (40, 40), (70, 70), 255, -1)
    out = pvd._filter_mask_by_cc(mask, 500)
    n, _, stats, _ = cv.connectedComponentsWithStats(out, connectivity=8)
    areas = [stats[i, cv.CC_STAT_AREA] for i in range(1, n)]
    assert areas == [961]


def test_diff_page_pair_writes_meta(tmp_path: Path) -> None:
    pvd._opencv.cache_clear()
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    img = Image.new("RGB", (80, 60), (200, 200, 200))
    img.save(a)
    img.save(b)
    res = pvd.diff_page_pair_pngs(a, b)
    assert res.overlay_path.is_file()
    meta = res.overlay_path.with_suffix(".meta.json")
    assert meta.is_file()
