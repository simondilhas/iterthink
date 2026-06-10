"""Tests for plan change region detection."""

from __future__ import annotations

from iterthink.services.plan_change_regions import (
    cluster_norm_bboxes,
    region_key_for,
)
from iterthink.tools import pdf_visual_diff as pvd


def test_cluster_norm_bboxes_merges_nearby() -> None:
    a = (0.1, 0.1, 0.2, 0.2)
    b = (0.12, 0.12, 0.22, 0.22)
    merged = cluster_norm_bboxes([(a, 100, ()), (b, 50, ("t1",))])
    assert len(merged) == 1
    nb, px, tids = merged[0]
    assert px == 150
    assert tids == ("t1",)
    assert nb[0] <= a[0]
    assert nb[2] >= b[2]


def test_region_key_stable() -> None:
    nb = (0.1, 0.2, 0.3, 0.4)
    assert region_key_for(0, nb) == region_key_for(0, nb)


def test_bboxes_from_mask_and_cluster() -> None:
    pvd._opencv.cache_clear()
    import numpy as np

    cv = pvd._opencv()
    mask_arr = np.zeros((100, 100), dtype=np.uint8)
    cv.rectangle(mask_arr, (20, 20), (60, 60), 255, -1)
    raw = pvd.bboxes_from_mask(mask_arr)
    assert len(raw) == 1
    clusters = pvd.cluster_bboxes_px(raw, page_w=100, page_h=100)
    assert len(clusters) == 1
    nb, px = clusters[0]
    assert px >= 100
    assert 0.0 <= nb[0] < nb[2] <= 1.0
