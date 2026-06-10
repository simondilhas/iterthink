"""Tight + context crops for plan change-region impact analysis."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from iterthink import config
from iterthink.persistence.content_repo import path_key_for
from iterthink.services.plan_region_context import crop_norm_bbox_from_page_png


@dataclass(frozen=True)
class RegionCropSet:
    crop_before: Path
    crop_after: Path
    context_crop: Path


def expand_norm_bbox_centered(
    nb: tuple[float, float, float, float],
    *,
    scale: float = 2.0,
) -> tuple[float, float, float, float]:
    """Expand bbox width/height by *scale* around center; clamp to page bounds."""
    x0, y0, x1, y1 = nb
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    hw = max(x1 - x0, 1e-6) * scale * 0.5
    hh = max(y1 - y0, 1e-6) * scale * 0.5
    return (
        max(0.0, cx - hw),
        max(0.0, cy - hh),
        min(1.0, cx + hw),
        min(1.0, cy + hh),
    )


def _write_crop_png(page_png: Path, norm_bbox: tuple[float, float, float, float], dest: Path) -> Path:
    data = crop_norm_bbox_from_page_png(page_png, norm_bbox)
    if not data:
        raise ValueError(f"Empty crop from {page_png}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def region_crop_dir(
    doc_path: Path,
    candidate_version_id: int,
    region_key: str,
) -> Path:
    pk = path_key_for(doc_path.resolve())
    return (
        config.STORE_DIR
        / "plan_region_crops"
        / pk
        / str(int(candidate_version_id))
        / region_key
    )


def build_region_crop_set(
    *,
    doc_path: Path,
    candidate_version_id: int,
    region_key: str,
    base_page_png: Path,
    cand_page_png: Path,
    norm_bbox: tuple[float, float, float, float],
    context_scale: float = 2.0,
) -> RegionCropSet:
    """Write before/after tight crops and 2x context crop on candidate page."""
    out_dir = region_crop_dir(doc_path, candidate_version_id, region_key)
    context_nb = expand_norm_bbox_centered(norm_bbox, scale=context_scale)
    before = _write_crop_png(base_page_png, norm_bbox, out_dir / "before.png")
    after = _write_crop_png(cand_page_png, norm_bbox, out_dir / "after.png")
    context = _write_crop_png(cand_page_png, context_nb, out_dir / "context.png")
    return RegionCropSet(crop_before=before, crop_after=after, context_crop=context)
