"""Export plan PDF with pin and revision-cloud annotations (pypdf)."""

from __future__ import annotations

from pathlib import Path

from iterthink.persistence.plan_pdf_annotations import (
    KIND_PIN,
    KIND_REVISION_CLOUD,
    PlanAnnotation,
)


def _page_size_pts(pdf_path: Path, page_index: int) -> tuple[float, float]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    page = reader.pages[int(page_index)]
    box = page.mediabox
    return float(box.width), float(box.height)


def _norm_to_pdf_rect(
    ann: PlanAnnotation,
    page_w: float,
    page_h: float,
) -> tuple[float, float, float, float]:
    if ann.annotation_kind == KIND_REVISION_CLOUD:
        bbox = ann.cloud_bbox_norm()
        if bbox is None:
            u = float(ann.plan_norm_x or 0.5)
            v = float(ann.plan_norm_y or 0.5)
            return (u * page_w - 20, (1 - v) * page_h - 20, u * page_w + 20, (1 - v) * page_h + 20)
        x0, y0, x1, y1 = bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]
        return (
            x0 * page_w,
            (1.0 - y1) * page_h,
            x1 * page_w,
            (1.0 - y0) * page_h,
        )
    u = float(ann.plan_norm_x or 0.5)
    v = float(ann.plan_norm_y or 0.5)
    px = u * page_w
    py = (1.0 - v) * page_h
    r = min(page_w, page_h) * 0.02
    return (px - r, py - r, px + r, py + r)


def export_annotated_pdf(
    source_pdf: Path,
    annotations: list[PlanAnnotation],
    output_pdf: Path,
) -> None:
    """Write ``output_pdf`` with Text and Rectangle markup annotations."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Rectangle, Text

    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()
    writer.append(reader)

    by_page: dict[int, list[PlanAnnotation]] = {}
    for a in annotations:
        by_page.setdefault(int(a.plan_page_index), []).append(a)

    for page_ix, page_anns in by_page.items():
        if page_ix < 0 or page_ix >= len(writer.pages):
            continue
        page_w, page_h = _page_size_pts(source_pdf, page_ix)
        for ann in page_anns:
            rect = _norm_to_pdf_rect(ann, page_w, page_h)
            if ann.annotation_kind == KIND_REVISION_CLOUD:
                writer.add_annotation(
                    page_ix,
                    Rectangle(
                        rect=rect,
                        interior_color="#B38FC133",
                    ),
                )
            elif ann.annotation_kind == KIND_PIN:
                body = (ann.body or "").strip() or "Comment"
                writer.add_annotation(
                    page_ix,
                    Text(
                        rect=rect,
                        text=body,
                        open=True,
                    ),
                )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as f:
        writer.write(f)
