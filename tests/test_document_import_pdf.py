"""Smoke tests for PDF import without PyMuPDF."""

from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_classify_sample_page_indices() -> None:
    assert document_import._classify_sample_page_indices(1) == [0]
    assert document_import._classify_sample_page_indices(3) == [0, 1, 2]
    ix = document_import._classify_sample_page_indices(100)
    assert ix[0] == 0
    assert ix[-1] == 99
    assert len(ix) <= 5


def test_classify_pdf_profile_samples_pages_not_all(tmp_path: Path) -> None:
    p = tmp_path / "many.pdf"
    w = PdfWriter()
    for _ in range(20):
        w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)

    reader_pages = [MagicMock() for _ in range(20)]
    for pg in reader_pages:
        pg.extract_text.return_value = ""

    reader = MagicMock()
    reader.pages = reader_pages

    with patch("pypdf.PdfReader", return_value=reader):
        assert document_import.classify_pdf_profile(p) == "plan"

    sampled = document_import._classify_sample_page_indices(20)
    for i in sampled:
        reader_pages[i].extract_text.assert_called()


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


def test_render_pdf_to_png_pages_multipage_max_pages(tmp_path: Path, monkeypatch) -> None:
    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "STORE_DIR", tmp_path / "store")
    (cfg.STORE_DIR).mkdir(parents=True, exist_ok=True)

    p = tmp_path / "multi.pdf"
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)

    first = document_import.render_pdf_to_png_pages(p, max_pages=1)
    assert len(first) == 1
    assert document_import.count_pdf_pages(p) == 3

    all_pages = document_import.render_pdf_to_png_pages(p)
    assert len(all_pages) == 3


def test_render_pdf_to_png_pages_concurrent(tmp_path: Path, monkeypatch) -> None:
    """pypdfium2 must not run concurrently (native heap corruption)."""
    import concurrent.futures

    import iterthink.config as cfg

    monkeypatch.setattr(cfg, "STORE_DIR", tmp_path / "store")
    (cfg.STORE_DIR).mkdir(parents=True, exist_ok=True)

    p = tmp_path / "multi.pdf"
    w = PdfWriter()
    for _ in range(5):
        w.add_blank_page(width=612, height=792)
    with open(p, "wb") as f:
        w.write(f)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = [
            pool.submit(document_import.render_pdf_to_png_pages, p, max_pages=3),
            pool.submit(document_import.render_pdf_to_png_pages, p),
            pool.submit(document_import.count_pdf_pages, p),
            pool.submit(document_import.render_pdf_to_png_pages, p, max_pages=1),
        ]
        results = [f.result() for f in futs]

    assert document_import.count_pdf_pages(p) == 5
    assert len(results[1]) == 5
    for batch in results:
        if isinstance(batch, list):
            for path in batch:
                assert path.is_file(), f"missing rendered page {path}"
