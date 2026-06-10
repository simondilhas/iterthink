"""Plan change-region context for future LLM impact analysis."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from sqlalchemy.orm import Session

from iterthink.persistence import plan_pdf_annotations


@dataclass(frozen=True)
class PlanRegionImpactContext:
    annotation_id: int
    region_key: str
    paragraph_index: int
    page_index: int
    norm_bbox: tuple[float, float, float, float]
    baseline_version_id: int
    candidate_version_id: int
    text_change_ids: tuple[str, ...]
    body: str


def list_region_contexts(
    session: Session, *, candidate_version_id: int
) -> list[PlanRegionImpactContext]:
    """Non-dismissed change regions on a candidate version, ready for impact/embed prep."""
    out: list[PlanRegionImpactContext] = []
    for ann in plan_pdf_annotations.list_change_regions_for_version(
        session, content_version_id=int(candidate_version_id)
    ):
        meta = ann.region_meta()
        if meta.get("dismissed"):
            continue
        bbox = ann.region_bbox_norm()
        if bbox is None:
            continue
        nb = (bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"])
        tids = tuple(str(t) for t in (meta.get("text_change_ids") or ()))
        out.append(
            PlanRegionImpactContext(
                annotation_id=int(ann.id),
                region_key=str(meta.get("region_key") or ""),
                paragraph_index=int(ann.paragraph_index),
                page_index=int(ann.plan_page_index),
                norm_bbox=nb,
                baseline_version_id=int(meta.get("baseline_version_id") or 0),
                candidate_version_id=int(candidate_version_id),
                text_change_ids=tids,
                body=(ann.body or "").strip(),
            )
        )
    return out


def crop_norm_bbox_from_page_png(
    page_png: Path,
    norm_bbox: tuple[float, float, float, float],
    *,
    pad_frac: float = 0.02,
) -> bytes:
    """Crop a normalized plan bbox from a rendered page PNG (for vision/embed inputs)."""
    from PIL import Image

    img = Image.open(page_png).convert("RGBA")
    w, h = img.size
    x0, y0, x1, y1 = norm_bbox
    pad_x = pad_frac * max(x1 - x0, 1e-6)
    pad_y = pad_frac * max(y1 - y0, 1e-6)
    px0 = max(0, int((x0 - pad_x) * w))
    py0 = max(0, int((y0 - pad_y) * h))
    px1 = min(w, int((x1 + pad_x) * w))
    py1 = min(h, int((y1 + pad_y) * h))
    if px1 <= px0 or py1 <= py0:
        return b""
    cropped = img.crop((px0, py0, px1, py1))
    buf = BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()
