"""Tests for RAG parent/child chunking."""

from __future__ import annotations

from iterthink.services.rag.chunking import build_parent_child_chunks, document_title


def test_document_title_from_h1() -> None:
    body = "# My Doc\n\nPara."
    assert document_title(body, "file.md") == "My Doc"


def test_document_title_fallback_filename() -> None:
    assert document_title("No heading", "notes.md") == "notes"


def test_parent_child_sections() -> None:
    body = "# Title\n\nIntro para.\n\n## Section A\n\nDetail one.\n\nDetail two."
    parents = build_parent_child_chunks(body, doc_title="Title", overlap_chars=50)
    assert len(parents) >= 2
    assert parents[0].section_header == "Title"
    assert any(p.section_header == "Section A" for p in parents)
    section_a = next(p for p in parents if p.section_header == "Section A")
    assert len(section_a.children) >= 2
    child = section_a.children[-1]
    assert "Detail two" in child.raw_text
    assert child.build_embed_text(doc_title="Title")
    assert "Title:" in child.build_embed_text(doc_title="Title")
    assert "Header:" in child.build_embed_text(doc_title="Title")


def test_overlap_included() -> None:
    body = "First paragraph here.\n\nSecond paragraph here."
    parents = build_parent_child_chunks(body, doc_title="T", overlap_chars=100)
    assert len(parents) == 1
    assert len(parents[0].children) == 2
    second = parents[0].children[1]
    assert "First paragraph" in second.overlap_text
