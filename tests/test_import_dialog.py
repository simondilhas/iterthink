"""Import dialog path hints and library stem normalization."""

from pathlib import Path

from iterthink import config
from iterthink.services import document_import
from iterthink.studio.explorer import (
    _effective_pdf_import_profile,
    _import_allowed_extensions,
)


def test_normalize_import_library_stem_strips_picker_suffix() -> None:
    stem = "P-Wetzikon-Metropol-Stadtgasse-SO-011-Layout-2025-10-16-B_1778956074828543379"
    assert document_import.normalize_import_library_stem(stem) == (
        "P-Wetzikon-Metropol-Stadtgasse-SO-011-Layout-2025-10-16-B"
    )


def test_normalize_import_library_stem_keeps_short_numeric_suffix() -> None:
    assert document_import.normalize_import_library_stem("Room_12") == "Room_12"


def test_import_dest_md_path_uses_normalized_stem(tmp_path: Path) -> None:
    src = tmp_path / "Plan_12345678901234.pdf"
    src.touch()
    dest = document_import.import_dest_md_path(src, tmp_path / "sub")
    assert dest is not None
    assert dest.name == "Plan.md"
    assert dest.parent == (tmp_path / "sub").resolve()


def test_import_target_display_path_subfolder(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    dest = root / "projects" / "site" / "floor.md"
    dest.parent.mkdir(parents=True)
    label = document_import.import_target_display_path(dest, root)
    assert label == "projects/site/floor.pdf"
    assert "import_staging" not in label
    assert "time_ns" not in label


def test_import_pdf_dialog_hint_new_vs_version(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    dest = root / "note.md"
    dest.parent.mkdir(parents=True)
    new_hint = document_import.import_pdf_dialog_hint(dest, root, import_into_existing=False)
    ver_hint = document_import.import_pdf_dialog_hint(dest, root, import_into_existing=True)
    assert new_hint == "Save as: ./note.pdf"
    assert ver_hint == "Add version to: ./note.pdf"
    assert "library file" not in new_hint
    assert "→" not in new_hint


def test_import_allowed_extensions_ocr_off(monkeypatch) -> None:
    monkeypatch.setattr(config, "OCR_ENABLED", False)
    assert _import_allowed_extensions() == ["docx", "pdf"]


def test_import_allowed_extensions_ocr_on(monkeypatch) -> None:
    monkeypatch.setattr(config, "OCR_ENABLED", True)
    exts = _import_allowed_extensions()
    assert exts[:2] == ["docx", "pdf"]
    assert "png" in exts
    assert "webp" in exts


def test_plan_pdf_import_coerce_disabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "PLAN_PDF_IMPORT_ENABLED", False)
    assert _effective_pdf_import_profile("plan") == "text"
    assert _effective_pdf_import_profile("text") == "text"


def test_plan_pdf_import_coerce_enabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "PLAN_PDF_IMPORT_ENABLED", True)
    assert _effective_pdf_import_profile("plan") == "plan"
