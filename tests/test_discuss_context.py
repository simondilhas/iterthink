"""Tests for Discuss-mode LLM context (selection vs full document)."""

from __future__ import annotations

from iterthink.studio.discuss_context import (
    discuss_llm_context_text,
    discuss_llm_uses_selection,
)


def test_empty_buffer() -> None:
    assert discuss_llm_context_text("", None) == ""
    assert not discuss_llm_uses_selection("", None)


def test_no_selection_returns_full_buffer() -> None:
    buf = "para one\n\npara two"
    assert discuss_llm_context_text(buf, None) == buf
    assert not discuss_llm_uses_selection(buf, None)


def test_selection_returns_substring() -> None:
    buf = "alpha\n\nbeta\n\ngamma"
    assert discuss_llm_context_text(buf, (7, 11)) == "beta"
    assert discuss_llm_uses_selection(buf, (7, 11))


def test_collapsed_or_invalid_selection_falls_back_to_full() -> None:
    buf = "hello world"
    assert discuss_llm_context_text(buf, (5, 5)) == buf
    assert not discuss_llm_uses_selection(buf, (5, 5))
    assert discuss_llm_context_text(buf, (0, 99)) == buf
    assert not discuss_llm_uses_selection(buf, (0, 99))


def test_whitespace_only_selection_falls_back_to_full() -> None:
    buf = "ab  cd"
    assert discuss_llm_context_text(buf, (2, 4)) == buf
    assert not discuss_llm_uses_selection(buf, (2, 4))


def test_truncation_at_max_chars() -> None:
    buf = "x" * 100
    assert len(discuss_llm_context_text(buf, None, max_chars=20)) == 20
    assert len(discuss_llm_context_text(buf, (0, 50), max_chars=10)) == 10
