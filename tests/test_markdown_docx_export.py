"""Tests for Word export (markdown → docx)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from docx import Document

from iterthink.services import docx_placeholders, markdown_docx_export
from iterthink.services.markdown_docx_export import ExportMeta


def _minimal_template(tmp_path: Path) -> Path:
    p = tmp_path / "tpl.docx"
    d = Document()
    d.add_paragraph("Title: {Titel} on {Date} by {Author}")
    d.save(p)
    return p


def test_list_docx_templates_includes_bundled_when_present() -> None:
    names = [lbl for lbl, _ in markdown_docx_export.list_docx_templates()]
    assert "Iterthink Standard" in names


def test_apply_docx_placeholders_replaces_in_body(tmp_path: Path) -> None:
    tpl = _minimal_template(tmp_path)
    doc = Document(str(tpl))
    docx_placeholders.apply_docx_placeholders(
        doc,
        {"{Titel}": "X", "{Date}": "2099-01-01", "{Author}": "Y", "{Name}": "Y"},
    )
    out = tmp_path / "out.docx"
    doc.save(out)
    assert "{Titel}" not in _document_xml_text(out)
    assert "Title: X on 2099-01-01 by Y" in _document_xml_text(out)


def test_markdown_to_docx_inserts_line_breaks_for_single_newlines(tmp_path: Path) -> None:
    """Single newlines inside a paragraph become w:br (Word), not collapsed spaces."""
    tpl = _minimal_template(tmp_path)
    md = tmp_path / "note.md"
    md.write_text("first line\nsecond line\n\nnew paragraph", encoding="utf-8")
    out = tmp_path / "out.docx"
    markdown_docx_export.markdown_to_docx(
        markdown_src=md.read_text(encoding="utf-8"),
        md_path=md,
        template_path=tpl,
        output_path=out,
        meta=ExportMeta(title_stem="note", author="A", date_iso="2099-02-02"),
    )
    body = _document_xml_text(out)
    assert "first line" in body
    assert "second line" in body
    assert "new paragraph" in body
    assert "<w:br" in body


def test_markdown_to_docx_placeholders_include_name(tmp_path: Path) -> None:
    tpl = tmp_path / "tpl.docx"
    d = Document()
    d.add_paragraph("By {Name} on {Date}")
    d.save(tpl)
    md = tmp_path / "note.md"
    md.write_text("Hi", encoding="utf-8")
    out = tmp_path / "out.docx"
    markdown_docx_export.markdown_to_docx(
        markdown_src=md.read_text(encoding="utf-8"),
        md_path=md,
        template_path=tpl,
        output_path=out,
        meta=ExportMeta(title_stem="note", author="Pat", date_iso="2099-03-03"),
    )
    body = _document_xml_text(out)
    assert "{Name}" not in body
    assert "Pat" in body


def _document_xml_text(docx: Path) -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_markdown_to_docx_smoke(tmp_path: Path) -> None:
    tpl = _minimal_template(tmp_path)
    md = tmp_path / "note.md"
    md.write_text("# H\n\nHello [^a]\n\n[^a]: Foot\n", encoding="utf-8")
    out = tmp_path / "out.docx"
    markdown_docx_export.markdown_to_docx(
        markdown_src=md.read_text(encoding="utf-8"),
        md_path=md,
        template_path=tpl,
        output_path=out,
        meta=ExportMeta(title_stem="note", author="A", date_iso="2099-02-02"),
    )
    assert out.is_file()
    body = _document_xml_text(out)
    assert "2099-02-02" in body
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "word/footnotes.xml" in names
