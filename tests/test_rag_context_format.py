"""Tests for RAG context formatting."""

from __future__ import annotations

from iterthink.services.rag.chunk_type import ChunkType
from iterthink.services.rag.context_format import format_rag_context_block


def test_format_prefers_parent_text() -> None:
    parent = "First paragraph with enough characters for context.\n\nSecond paragraph here too."
    raw = "Second paragraph here too."
    block = format_rag_context_block(
        fname="doc.md",
        doc_title="Doc",
        section_header="Section",
        parent_text=parent,
        raw_text=raw,
        slot_index=1,
        chunk_type=ChunkType.UNKNOWN,
        max_chars=5000,
    )
    assert block is not None
    assert "First paragraph" in block
    assert "Second paragraph" in block


def test_format_falls_back_to_raw() -> None:
    raw = "Standalone paragraph with enough characters for norm context."
    block = format_rag_context_block(
        fname="doc.md",
        parent_text="",
        raw_text=raw,
        slot_index=0,
        chunk_type=ChunkType.UNKNOWN,
        max_chars=5000,
    )
    assert block is not None
    assert "Standalone paragraph" in block
