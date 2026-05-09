"""Unit tests for iterthink.compare.diff_card (inline diff spans)."""

from __future__ import annotations

import flet as ft

from iterthink.compare.diff_card import (
    build_new_side_spans,
    build_old_side_spans,
    build_unified_spans,
)


def _span_text(spans: list[ft.TextSpan]) -> str:
    return "".join(s.text or "" for s in spans)


def test_build_unified_spans_equal_text_single_style_chunk() -> None:
    spans = build_unified_spans("hello", "hello")
    assert _span_text(spans) == "hello"


def test_build_unified_spans_insertion_visible_on_new_side() -> None:
    spans = build_unified_spans("hello", "hello world")
    assert "hello" in _span_text(spans)
    assert "world" in _span_text(spans)


def test_build_unified_spans_deletion_still_lists_removed_chars() -> None:
    spans = build_unified_spans("hello world", "hello")
    joined = _span_text(spans)
    assert "hello" in joined
    assert "world" in joined


def test_build_old_side_spans_skips_insertions() -> None:
    joined = _span_text(build_old_side_spans("a b", "a c"))
    assert "b" in joined
    assert "c" not in joined


def test_build_new_side_spans_skips_deletions() -> None:
    joined = _span_text(build_new_side_spans("a b", "a c"))
    assert "c" in joined
    assert "b" not in joined


def test_build_unified_spans_both_empty_yields_space_placeholder() -> None:
    spans = build_unified_spans("", "")
    assert len(spans) >= 1
    assert _span_text(spans).strip() == ""
