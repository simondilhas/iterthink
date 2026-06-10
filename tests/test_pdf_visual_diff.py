"""Tests for PDF visual diff overlays."""

import threading
import time
from pathlib import Path

import numpy as np
import pytest
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


def test_bboxes_from_mask_returns_components() -> None:
    pvd._opencv.cache_clear()
    cv = pvd._opencv()
    mask = np.zeros((80, 80), dtype=np.uint8)
    cv.rectangle(mask, (10, 10), (30, 30), 255, -1)
    boxes = pvd.bboxes_from_mask(mask)
    assert len(boxes) == 1
    assert boxes[0][4] >= 400


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


def test_overlay_diff_identical_pages_near_zero_highlight() -> None:
    pvd._opencv.cache_clear()
    h, w = 120, 120
    a = _white_bgr(h, w)
    b = a.copy()
    ga, gb = pvd._to_gray(a), pvd._to_gray(b)
    out = pvd._overlay_diff(a, b, ga, gb, pdf_profile="plan")
    highlighted = (out[:, :, 1] == 200) | (out[:, :, 2] == 220)
    assert int(np.count_nonzero(highlighted)) == 0


def test_concurrent_overlay_builds_keep_png_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two overlapping diffs of the same PDF pair must not delete/clobber each
    other's overlay PNGs (regression: Review overlay blanked out after ~2s)."""
    pvd._opencv.cache_clear()
    monkeypatch.setattr(pvd.config, "STORE_DIR", tmp_path)
    cv = pvd._opencv()

    pdf_a = tmp_path / "a.pdf"
    pdf_b = tmp_path / "b.pdf"
    pdf_a.write_bytes(b"a")
    pdf_b.write_bytes(b"b")

    pages_a: list[Path] = []
    pages_b: list[Path] = []
    for i in range(3):
        pa = tmp_path / f"a_{i}.png"
        pb = tmp_path / f"b_{i}.png"
        a = _white_bgr(120, 120)
        b = a.copy()
        b[30:80, 30:80] = (0, 0, 0)
        cv.imwrite(str(pa), a)
        cv.imwrite(str(pb), b)
        pages_a.append(pa)
        pages_b.append(pb)

    def fake_render(pdf: Path, *, pdf_profile: str = "plan") -> list[Path]:
        return pages_a if pdf == pdf_a else pages_b

    monkeypatch.setattr(pvd, "render_pdf_to_png_pages", fake_render)

    orig_overlay = pvd._overlay_diff

    def slow_overlay(*args: object, **kwargs: object) -> object:
        time.sleep(0.02)
        return orig_overlay(*args, **kwargs)

    monkeypatch.setattr(pvd, "_overlay_diff", slow_overlay)

    results: dict[str, tuple[list[Path], str | None, list[float]]] = {}

    def run(key: str) -> None:
        results[key] = pvd.diff_pdfs_to_overlay_paths(pdf_a, pdf_b, pdf_profile="plan")

    t1 = threading.Thread(target=run, args=("t1",))
    t2 = threading.Thread(target=run, args=("t2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert set(results) == {"t1", "t2"}
    for key, (paths, _warn, _confs) in results.items():
        assert paths, f"{key} returned no overlay paths"
        for p in paths:
            assert p.is_file(), f"{key}: overlay PNG {p} missing on disk"


def test_overlay_diff_mask_matches_diff_mask_for_page_pair(tmp_path: Path) -> None:
    pvd._opencv.cache_clear()
    h, w = 120, 120
    a = _white_bgr(h, w)
    b = a.copy()
    b[35:85, 35:85] = (0, 0, 0)
    path_a = tmp_path / "a.png"
    path_b = tmp_path / "b.png"
    pvd._opencv().imwrite(str(path_a), a)
    pvd._opencv().imwrite(str(path_b), b)
    ga, gb_aligned, _, _, _ = pvd._prepare_aligned_page_pair(path_a, path_b)
    overlay_mask = pvd._diff_mask_from_aligned_grays(ga, gb_aligned, pdf_profile="plan")
    pair_mask, _, _ = pvd.diff_mask_for_page_pair(path_a, path_b, pdf_profile="plan")
    assert np.array_equal(overlay_mask, pair_mask)
