"""Smoke tests for annotated plan PDF export."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter

from iterthink.persistence.plan_pdf_annotations import (
    KIND_PIN,
    KIND_REVISION_CLOUD,
    PlanAnnotation,
)
from iterthink.services.plan_pdf_export import export_annotated_pdf


def _minimal_pdf(path: Path) -> None:
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with path.open("wb") as f:
        w.write(f)


def test_export_annotated_pdf_writes_file(tmp_path: Path) -> None:
    src = tmp_path / "plan.pdf"
    out = tmp_path / "out.pdf"
    _minimal_pdf(src)
    anns = [
        PlanAnnotation(
            id=1,
            content_version_id=1,
            paragraph_index=0,
            annotation_kind=KIND_PIN,
            plan_page_index=0,
            plan_norm_x=0.5,
            plan_norm_y=0.5,
            body="Test pin",
            geometry_json=None,
        ),
        PlanAnnotation(
            id=2,
            content_version_id=1,
            paragraph_index=1,
            annotation_kind=KIND_REVISION_CLOUD,
            plan_page_index=0,
            plan_norm_x=0.5,
            plan_norm_y=0.5,
            body="",
            geometry_json='{"x0":0.1,"y0":0.1,"x1":0.4,"y1":0.3}',
        ),
    ]
    export_annotated_pdf(src, anns, out)
    assert out.is_file()
    reader = PdfReader(str(out))
    assert len(reader.pages) == 1
    annots = reader.pages[0].get("/Annots")
    assert annots is not None
