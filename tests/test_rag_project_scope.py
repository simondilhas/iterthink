"""Tests for RAG project scope helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterthink.services.rag.chunking import ChildChunk
from iterthink.services.rag.project_scope import project_slug_for_path


def test_project_slug_for_nested_doc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "AlphaProj" / "notes.md"
    md.parent.mkdir()
    md.touch()
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)
    assert project_slug_for_path(md) == "AlphaProj"


def test_project_slug_none_at_documents_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "root.md"
    md.touch()
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)
    assert project_slug_for_path(md) is None


def test_build_embed_text_includes_project() -> None:
    child = ChildChunk(
        slot_index=0,
        raw_text="Body text here with enough length.",
        section_header="Sec",
        overlap_text="",
    )
    text = child.build_embed_text(doc_title="Doc", project_label="MyProj")
    assert text.startswith("Project: MyProj")
    assert "Title: Doc" in text
