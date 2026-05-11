"""Smoke tests for PDF import without PyMuPDF."""

from pathlib import Path

from pypdf import PdfWriter

from iterthink.services import document_import


def test_pdf_to_markdown_blank_page(tmp_path: Path) -> None:
    p = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)
    md = document_import.pdf_to_markdown(p)
    assert "<!-- page:1 -->" in md


def test_classify_pdf_profile_blank(tmp_path: Path) -> None:
    p = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)
    assert document_import.classify_pdf_profile(p) == "plan"


def test_render_pdf_to_png_pages(tmp_path: Path, monkeypatch) -> None:
    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "STORE_DIR", tmp_path / "store")
    (cfg.STORE_DIR).mkdir(parents=True, exist_ok=True)

    p = tmp_path / "one.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)

    paths = document_import.render_pdf_to_png_pages(p)
    assert len(paths) == 1
    assert paths[0].suffix == ".png"
    assert paths[0].is_file()
