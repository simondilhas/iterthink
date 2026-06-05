"""Diff plan PDF geometry sidecars (baseline vs candidate) for text-change overlays."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from typing import Literal

PlanTextChangeKind = Literal["stable", "modified", "added", "removed"]

_BBOX_IOU_MIN = 0.25
_CENTER_DIST_FRAC = 0.02
_PIN_OFFSET_U = 0.006
_PIN_OFFSET_V = 0.0


@dataclass(frozen=True)
class PlanTextChangeView:
    change_id: str
    page_index: int
    kind: PlanTextChangeKind
    norm_bbox: tuple[float, float, float, float]
    display_text: str
    old_text: str | None
    new_text: str | None
    size_pt: float
    pin_norm: tuple[float, float] | None


def _page_map(geometry: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for p in geometry.get("pages") or []:
        pi = int(p.get("page") or 0) - 1
        if pi >= 0:
            out[pi] = p
    return out


def _norm_bbox(
    bbox: list[float], page_w: float, page_h: float
) -> tuple[float, float, float, float]:
    pw = max(float(page_w), 1e-6)
    ph = max(float(page_h), 1e-6)
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return (x0 / pw, y0 / ph, x1 / pw, y1 / ph)


def _bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
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


def _bbox_center(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)


def _center_dist(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay = _bbox_center(a)
    bx, by = _bbox_center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _bbox_x_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return max(a[0], b[0]) < min(a[2], b[2])


def _lines_match_spatially(
    bb: tuple[float, float, float, float], cb: tuple[float, float, float, float]
) -> bool:
    iou = _bbox_iou(bb, cb)
    if iou >= _BBOX_IOU_MIN:
        return True
    if iou <= 0.0:
        return False
    diag = 2**0.5
    dist_max = _CENTER_DIST_FRAC * diag
    if _center_dist(bb, cb) >= dist_max:
        return False
    return _bbox_x_overlap(bb, cb)


def _pin_norm_for_bbox(nb: tuple[float, float, float, float]) -> tuple[float, float]:
    u = min(1.0, max(0.0, nb[2] + _PIN_OFFSET_U))
    v = min(1.0, max(0.0, nb[1] + _PIN_OFFSET_V))
    return (u, v)


def _change_id(page_index: int, kind: str, text: str, nb: tuple[float, float, float, float]) -> str:
    raw = f"{page_index}:{kind}:{text}:{nb[0]:.4f}:{nb[1]:.4f}:{nb[2]:.4f}:{nb[3]:.4f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _line_entries(page: dict) -> list[tuple[str, tuple[float, float, float, float], float]]:
    pw = float(page.get("width") or 612.0)
    ph = float(page.get("height") or 792.0)
    out: list[tuple[str, tuple[float, float, float, float], float]] = []
    for line in page.get("lines") or []:
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        bbox = line.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        nb = _norm_bbox(bbox, pw, ph)
        size_pt = float(line.get("size") or 11.0)
        out.append((text, nb, size_pt))
    return out


def _match_pairs(
    base_lines: list[tuple[str, tuple[float, float, float, float], float]],
    cand_lines: list[tuple[str, tuple[float, float, float, float], float]],
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """Greedy match by spatial proximity; returns (pairs, unmatched_base, unmatched_cand)."""
    if not base_lines and not cand_lines:
        return [], set(), set()
    diag = 2**0.5
    dist_max = _CENTER_DIST_FRAC * diag

    candidates: list[tuple[float, int, int]] = []
    for bi, (_bt, bb, _bs) in enumerate(base_lines):
        for ci, (_ct, cb, _cs) in enumerate(cand_lines):
            if not _lines_match_spatially(bb, cb):
                continue
            iou = _bbox_iou(bb, cb)
            cd = _center_dist(bb, cb)
            dist_max = _CENTER_DIST_FRAC * diag
            score = iou * 2.0 + (1.0 - min(cd / dist_max, 1.0)) if dist_max > 0 else iou
            candidates.append((score, bi, ci))

    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_b: set[int] = set()
    used_c: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _score, bi, ci in candidates:
        if bi in used_b or ci in used_c:
            continue
        used_b.add(bi)
        used_c.add(ci)
        pairs.append((bi, ci))

    unmatched_b = {i for i in range(len(base_lines))} - used_b
    unmatched_c = {i for i in range(len(cand_lines))} - used_c
    return pairs, unmatched_b, unmatched_c


def _make_view(
    *,
    page_index: int,
    kind: PlanTextChangeKind,
    nb: tuple[float, float, float, float],
    display_text: str,
    old_text: str | None,
    new_text: str | None,
    size_pt: float,
) -> PlanTextChangeView:
    pin = _pin_norm_for_bbox(nb) if kind in ("modified", "added", "removed") else None
    return PlanTextChangeView(
        change_id=_change_id(page_index, kind, display_text, nb),
        page_index=page_index,
        kind=kind,
        norm_bbox=nb,
        display_text=display_text,
        old_text=old_text,
        new_text=new_text,
        size_pt=size_pt,
        pin_norm=pin,
    )


def diff_plan_geometry(base: dict, cand: dict) -> list[PlanTextChangeView]:
    """
    Compare two plan geometry sidecars; return per-line change views (all pages).
    """
    base_pages = _page_map(base)
    cand_pages = _page_map(cand)
    all_page_indices = sorted(set(base_pages.keys()) | set(cand_pages.keys()))
    views: list[PlanTextChangeView] = []

    for pi in all_page_indices:
        base_lines = _line_entries(base_pages.get(pi, {"width": 612, "height": 792, "lines": []}))
        cand_lines = _line_entries(cand_pages.get(pi, {"width": 612, "height": 792, "lines": []}))
        pairs, unmatched_b, unmatched_c = _match_pairs(base_lines, cand_lines)

        for bi, ci in pairs:
            bt, bb, bs = base_lines[bi]
            ct, cb, cs = cand_lines[ci]
            if bt == ct:
                views.append(
                    _make_view(
                        page_index=pi,
                        kind="stable",
                        nb=cb,
                        display_text=ct,
                        old_text=bt,
                        new_text=ct,
                        size_pt=cs,
                    )
                )
            else:
                views.append(
                    _make_view(
                        page_index=pi,
                        kind="modified",
                        nb=cb,
                        display_text=ct,
                        old_text=bt,
                        new_text=ct,
                        size_pt=cs,
                    )
                )

        for bi in sorted(unmatched_b):
            bt, bb, bs = base_lines[bi]
            best_ci = -1
            best_ratio = 0.0
            for ci in unmatched_c:
                ct, _cb, _cs = cand_lines[ci]
                ratio = difflib.SequenceMatcher(None, bt, ct).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_ci = ci
            if best_ci >= 0 and best_ratio >= 0.85:
                unmatched_c.discard(best_ci)
                ct, cb, cs = cand_lines[best_ci]
                views.append(
                    _make_view(
                        page_index=pi,
                        kind="modified",
                        nb=cb,
                        display_text=ct,
                        old_text=bt,
                        new_text=ct,
                        size_pt=cs,
                    )
                )
            else:
                views.append(
                    _make_view(
                        page_index=pi,
                        kind="removed",
                        nb=bb,
                        display_text=bt,
                        old_text=bt,
                        new_text=None,
                        size_pt=bs,
                    )
                )

        for ci in sorted(unmatched_c):
            ct, cb, cs = cand_lines[ci]
            views.append(
                _make_view(
                    page_index=pi,
                    kind="added",
                    nb=cb,
                    display_text=ct,
                    old_text=None,
                    new_text=ct,
                    size_pt=cs,
                )
            )

    return views


def geometry_to_label_views(geometry: dict) -> list[PlanTextChangeView]:
    """Single-version sidecar → stable label views (no diff pins)."""
    views: list[PlanTextChangeView] = []
    for p in geometry.get("pages") or []:
        pi = int(p.get("page") or 0) - 1
        if pi < 0:
            continue
        pw = float(p.get("width") or 612.0)
        ph = float(p.get("height") or 792.0)
        for line in p.get("lines") or []:
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            bbox = line.get("bbox") or [0.0, 0.0, 0.0, 0.0]
            nb = _norm_bbox(bbox, pw, ph)
            size_pt = float(line.get("size") or 11.0)
            views.append(
                _make_view(
                    page_index=pi,
                    kind="stable",
                    nb=nb,
                    display_text=text,
                    old_text=text,
                    new_text=text,
                    size_pt=size_pt,
                )
            )
    return views
