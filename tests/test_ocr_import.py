"""OCR import orchestration."""

from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from iterthink.services import document_import, ocr_import


def test_image_to_markdown_copies_asset_and_ocr(tmp_path: Path, monkeypatch) -> None:
    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "IMPORT_ASSETS_DIR", tmp_path / "assets")
    src = tmp_path / "scan.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    dest_md = tmp_path / "notes" / "scan.md"

    with patch("iterthink.services.ocr_import._ocr_image_path", return_value="Hello scan"):
        md = ocr_import.image_to_markdown(src, dest_md)

    assert "Hello scan" in md
    assert "![scan]" in md
    assert (cfg.IMPORT_ASSETS_DIR / "scan" / "scan.png").is_file()


def test_pdf_to_markdown_ocr_page_markers(tmp_path: Path, monkeypatch) -> None:
    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "STORE_DIR", tmp_path / "store")
    (cfg.STORE_DIR).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "OCR_ENGINE", "rapidocr")

    p = tmp_path / "scan.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)

    fake_png = tmp_path / "page_0001.png"
    fake_png.write_bytes(b"x")

    with (
        patch("iterthink.services.document_import.render_pdf_to_png_pages", return_value=[fake_png]),
        patch("iterthink.services.ocr_import._ocr_image_path", return_value="Page one text"),
    ):
        md = ocr_import.pdf_to_markdown_ocr(p)

    assert "<!-- page:1 -->" in md
    assert "Page one text" in md


def test_pdf_to_markdown_uses_ocr_when_enabled(tmp_path: Path, monkeypatch) -> None:
    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "OCR_ENABLED", True)

    p = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)

    with patch(
        "iterthink.services.ocr_import.pdf_to_markdown_ocr",
        return_value="<!-- page:1 -->\n\nOCR body",
    ) as mocked:
        md = document_import.pdf_to_markdown(p)

    mocked.assert_called_once_with(p)
    assert "OCR body" in md


def test_allowed_import_extensions_include_images() -> None:
    assert "png" in document_import.ALLOWED_IMPORT_EXTENSIONS
    assert "webp" in document_import.ALLOWED_IMPORT_EXTENSIONS
