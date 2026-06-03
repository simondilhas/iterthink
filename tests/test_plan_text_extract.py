"""Tests for plan PDF geometry extraction and stub markdown."""

from pathlib import Path

from pypdf import PdfWriter

from iterthink.services import document_import
from iterthink.services.plan_text_extract import (
    composite_overlay_png,
    extract_plan_geometry,
    plan_stub_markdown,
    write_plan_text_sidecar,
)


def test_plan_stub_markdown_marker() -> None:
    geo = {"pages": [{"page": 1, "width": 100.0, "height": 100.0, "lines": [{"text": "A-1", "bbox": [0, 0, 1, 1], "size": 10}]}]}
    md = plan_stub_markdown(geo)
    assert "<!-- pdf_profile:plan -->" in md
    assert "A-1" not in md


def test_import_pdf_with_profile_forced_text(tmp_path: Path) -> None:
    p = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)
    md = document_import.import_pdf_with_profile(p, "text")
    assert "<!-- pdf_profile:plan -->" not in md


def test_import_pdf_for_profile_plan_blank(tmp_path: Path) -> None:
    p = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)
    md, prof = document_import.import_pdf_for_profile(p)
    assert prof == "plan"
    assert "<!-- pdf_profile:plan -->" in md


def test_composite_overlay_in_bbox(tmp_path: Path) -> None:
    from PIL import Image

    page_png = tmp_path / "page_0001.png"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(page_png)
    geom = {
        "lines": [
            {"text": "A-1", "bbox": [10.0, 10.0, 50.0, 22.0], "size": 10.0},
        ]
    }
    scale = 2.0
    out = composite_overlay_png(page_png, geom, scale=scale, show_labels=True, show_boxes=False)
    assert out.is_file()
    assert out.stat().st_size > 0

    before = Image.open(page_png).convert("RGB")
    after = Image.open(out).convert("RGB")
    assert before.size == after.size
    # Scaled bbox is [20,20,100,44]; glyph pixels land near (25, 32).
    ix, iy = 25, 32
    assert before.getpixel((ix, iy)) == (255, 255, 255)
    assert after.getpixel((ix, iy)) != (255, 255, 255)


def test_write_plan_text_sidecar(tmp_path: Path, monkeypatch) -> None:
    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "STORE_DIR", tmp_path / "store")
    (cfg.STORE_DIR).mkdir(parents=True, exist_ok=True)
    doc = tmp_path / "docs" / "plan.md"
    doc.parent.mkdir(parents=True)
    p = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)
    geo = document_import.extract_pdf_pages_geometry(p)
    path = write_plan_text_sidecar(doc.resolve(), 42, geo)
    assert path.is_file()
    assert path.name == "42.json"


def test_get_version_pdf_profile_plan_vs_text(tmp_path: Path, monkeypatch) -> None:
    """Plan and text PDF profiles persist on the version row for compare routing."""
    import iterthink.config as cfg
    from iterthink.db.session import session_scope
    from iterthink.persistence import version_storage

    store = tmp_path / "store"
    monkeypatch.setattr(cfg, "STORE_DIR", store)
    store.mkdir(parents=True, exist_ok=True)
    doc = tmp_path / "docs" / "note.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("<!-- pdf_profile:plan -->\n", encoding="utf-8")
    pdf = tmp_path / "drawing.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(pdf, "wb") as f:
        w.write(f)

    with session_scope() as s:
        plan_vid = version_storage.persist_version_snapshot(
            s,
            doc.resolve(),
            doc.read_text(encoding="utf-8"),
            "import",
            skip_if_unchanged_sha=False,
            pdf_source_path=pdf,
            pdf_profile="plan",
        )
        assert plan_vid is not None
        assert version_storage.get_version_pdf_profile(s, plan_vid) == "plan"

    md_text, prof = document_import.import_pdf_for_profile(pdf)
    assert prof == "plan"
    assert "<!-- pdf_profile:plan -->" in md_text

    text_md = document_import.import_pdf_with_profile(pdf, "text")
    assert "<!-- pdf_profile:plan -->" not in text_md
