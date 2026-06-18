"""Smoke tests for PDF import without PyMuPDF."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter

from iterthink.services import document_import


def _synthetic_pdf_line(text: str, y0: float, *, size: float = 10.0) -> dict:
    y1 = y0 + 12.0
    return {
        "bbox": [0.0, y0, 500.0, y1],
        "spans": [{"text": text, "size": size}],
    }


def test_pdf_compute_body_med_prefers_median() -> None:
    sizes = [8.0, 8.0, 8.0, 10.0, 10.0, 10.0, 10.0, 10.0]
    assert document_import._pdf_compute_body_med(sizes) == 10.0


def test_pdf_wrapped_body_run_not_headings() -> None:
    body_med = 10.0
    line1 = _synthetic_pdf_line("Ausgangspunkt eines systematischen", 100.0, size=11.2)
    line2 = _synthetic_pdf_line("Erhaltungsplanungsprozesses ist die Erhebung", 113.0, size=11.2)
    line3 = _synthetic_pdf_line("der erforderlichen Datengrundlagen.", 126.0, size=11.2)
    run = [
        ("Ausgangspunkt eines systematischen", 100.0, 112.0, line1),
        ("Erhaltungsplanungsprozesses ist die Erhebung", 113.0, 125.0, line2),
        ("der erforderlichen Datengrundlagen.", 126.0, 138.0, line3),
    ]
    events = document_import._pdf_classify_line_run(run, body_med)
    assert all(ev[0] == "body" for ev in events)
    md = document_import._pdf_events_to_markdown(events, body_med)
    assert "###" not in md
    assert "Ausgangspunkt" in md
    assert "Datengrundlagen." in md


def test_pdf_single_title_line_stays_heading() -> None:
    body_med = 10.0
    line = _synthetic_pdf_line("Beobachtung, Inspektion und Beurteilung", 80.0, size=12.8)
    run = [("Beobachtung, Inspektion und Beurteilung", 80.0, 92.0, line)]
    events = document_import._pdf_classify_line_run(run, body_med)
    assert events[0][0] == "h2"
    md = document_import._pdf_events_to_markdown(events, body_med)
    assert md.startswith("## Beobachtung")


def test_pdf_wrapped_title_run_becomes_one_heading() -> None:
    body_med = 10.0
    line1 = _synthetic_pdf_line("Beobachtung, Inspektion", 80.0, size=13.0)
    line2 = _synthetic_pdf_line("und Beurteilung", 93.0, size=13.0)
    run = [
        ("Beobachtung, Inspektion", 80.0, 92.0, line1),
        ("und Beurteilung", 93.0, 105.0, line2),
    ]
    events = document_import._pdf_classify_line_run(run, body_med)
    assert len(events) == 1
    assert events[0][0] == "h2"
    assert events[0][1][0] == "Beobachtung, Inspektion und Beurteilung"
    md = document_import._pdf_events_to_markdown(events, body_med)
    assert md == "## Beobachtung, Inspektion und Beurteilung"


def test_pdf_hyphen_soft_break_join() -> None:
    body_med = 10.0
    events = [
        ("body", ("Ach-", 100.0, 112.0)),
        ("body", ("sen und", 113.0, 125.0)),
    ]
    md = document_import._pdf_events_to_markdown(events, body_med)
    assert md == "Achsen und"


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
