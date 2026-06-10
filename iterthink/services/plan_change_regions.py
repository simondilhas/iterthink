"""Auto-detect dense plan change regions from pixel diff + text geometry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from iterthink.services.document_import import render_pdf_to_png_pages
from iterthink.services.plan_text_diff import PlanTextChangeView, diff_plan_geometry
from iterthink.tools import pdf_visual_diff as pvd

_MIN_PIXEL_COUNT = 300
_NORM_IOU_MERGE = 0.05
_NORM_DIST_FRAC = 0.03
_NORM_PAD_FRAC = 0.02


@dataclass(frozen=True)
class DetectedChangeRegion:
    """Ephemeral detection result before DB sync."""

    region_key: str
    page_index: int
    norm_bbox: tuple[float, float, float, float]
    pixel_count: int = 0
    text_change_ids: tuple[str, ...] = ()


@dataclass
class PlanChangeRegionView:
    """Region ready for UI overlay and KI Comments."""

    region_id: int
    page_index: int
    norm_bbox: tuple[float, float, float, float]
    paragraph_index: int
    body: str
    pixel_count: int
    text_change_ids: tuple[str, ...]
    dismissed: bool
    reviewed: bool
    region_key: str


def region_key_for(page_index: int, nb: tuple[float, float, float, float]) -> str:
    raw = f"{page_index}:{nb[0]:.3f}:{nb[1]:.3f}:{nb[2]:.3f}:{nb[3]:.3f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _norm_iou(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 1e-9 else 0.0


def _norm_center(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)


def _union_norm(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return (x0, y0, x1, y1)


def _pad_norm(
    nb: tuple[float, float, float, float], pad_frac: float = _NORM_PAD_FRAC
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = nb
    pw, ph = x1 - x0, y1 - y0
    return (
        max(0.0, x0 - pad_frac * pw),
        max(0.0, y0 - pad_frac * ph),
        min(1.0, x1 + pad_frac * pw),
        min(1.0, y1 + pad_frac * ph),
    )


def cluster_norm_bboxes(
    entries: list[tuple[tuple[float, float, float, float], int, tuple[str, ...]]],
) -> list[tuple[tuple[float, float, float, float], int, tuple[str, ...]]]:
    """Merge normalized bboxes; each entry is (bbox, pixel_count, text_change_ids)."""
    if not entries:
        return []
    dist_max = _NORM_DIST_FRAC * (2**0.5)
    clusters: list[tuple[list[tuple[float, float, float, float]], int, list[str]]] = []
    for nb, px, tids in entries:
        merged = False
        for idx, (boxes, total_px, all_tids) in enumerate(clusters):
            for b in boxes:
                if _norm_iou(b, nb) >= _NORM_IOU_MERGE:
                    clusters[idx] = (boxes + [nb], total_px + px, all_tids + list(tids))
                    merged = True
                    break
                ca = _norm_center(b)
                cb = _norm_center(nb)
                if ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5 <= dist_max:
                    clusters[idx] = (boxes + [nb], total_px + px, all_tids + list(tids))
                    merged = True
                    break
            if merged:
                break
        if not merged:
            clusters.append(([nb], px, list(tids)))

    out: list[tuple[tuple[float, float, float, float], int, tuple[str, ...]]] = []
    for boxes, total_px, all_tids in clusters:
        ub = _pad_norm(_union_norm(boxes))
        if ub[2] - ub[0] < 0.005 or ub[3] - ub[1] < 0.005:
            continue
        out.append((ub, total_px, tuple(dict.fromkeys(all_tids))))
    return out


def _pixel_regions_for_page_pair(
    path_a: Path, path_b: Path, page_index: int
) -> list[DetectedChangeRegion]:
    mask, tw, th = pvd.diff_mask_for_page_pair(path_a, path_b, pdf_profile="plan")
    raw = pvd.bboxes_from_mask(mask)
    clusters = pvd.cluster_bboxes_px(raw, page_w=tw, page_h=th)
    out: list[DetectedChangeRegion] = []
    for nb, px in clusters:
        if px < _MIN_PIXEL_COUNT:
            continue
        rk = region_key_for(page_index, nb)
        out.append(
            DetectedChangeRegion(
                region_key=rk,
                page_index=page_index,
                norm_bbox=nb,
                pixel_count=px,
            )
        )
    return out


def _text_regions_from_changes(
    changes: list[PlanTextChangeView],
) -> list[tuple[int, tuple[float, float, float, float], tuple[str, ...]]]:
    """Per-page clusters from non-stable text changes."""
    by_page: dict[int, list[PlanTextChangeView]] = {}
    for ch in changes:
        if ch.kind == "stable":
            continue
        by_page.setdefault(int(ch.page_index), []).append(ch)

    out: list[tuple[int, tuple[float, float, float, float], tuple[str, ...]]] = []
    for pi, page_changes in by_page.items():
        entries = [(ch.norm_bbox, 0, (ch.change_id,)) for ch in page_changes]
        for nb, _, tids in cluster_norm_bboxes(entries):
            out.append((pi, nb, tids))
    return out


def detect_change_regions(
    base_pdf: Path,
    cand_pdf: Path,
    base_geo: dict,
    cand_geo: dict,
) -> list[DetectedChangeRegion]:
    """
    Compare baseline vs candidate plan PDFs and geometry sidecars.
    Returns deduplicated regions per page (pixel + text merged).
    """
    pages_a = render_pdf_to_png_pages(base_pdf, pdf_profile="plan")
    pages_b = render_pdf_to_png_pages(cand_pdf, pdf_profile="plan")
    n = min(len(pages_a), len(pages_b))
    if n == 0:
        return []

    text_changes = diff_plan_geometry(base_geo, cand_geo)
    text_entries = _text_regions_from_changes(text_changes)

    per_page: dict[int, list[tuple[tuple[float, float, float, float], int, tuple[str, ...]]]] = {}

    for i in range(n):
        for reg in _pixel_regions_for_page_pair(pages_a[i], pages_b[i], i):
            per_page.setdefault(i, []).append((reg.norm_bbox, reg.pixel_count, ()))

    for pi, nb, tids in text_entries:
        per_page.setdefault(pi, []).append((nb, 0, tids))

    merged: list[DetectedChangeRegion] = []
    for pi in sorted(per_page.keys()):
        for nb, px, tids in cluster_norm_bboxes(per_page[pi]):
            keep = px >= _MIN_PIXEL_COUNT or len(tids) >= 1
            if not keep:
                continue
            rk = region_key_for(pi, nb)
            merged.append(
                DetectedChangeRegion(
                    region_key=rk,
                    page_index=pi,
                    norm_bbox=nb,
                    pixel_count=px,
                    text_change_ids=tids,
                )
            )
    return merged
