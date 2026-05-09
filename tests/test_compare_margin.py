"""Unit tests for iterthink.compare.margin (paragraph splitting and editor helpers)."""

from __future__ import annotations

import pytest

from iterthink.compare.margin import (
    distribute_heights,
    join_paragraphs,
    paragraph_index_at_offset,
    replace_paragraph_at_index,
    split_paragraphs,
    wrapped_line_count,
)


def test_split_paragraphs_empty_yields_single_empty_slot() -> None:
    assert split_paragraphs("") == [""]


def test_split_paragraphs_double_newline_separates_blocks() -> None:
    assert split_paragraphs("a\n\nb") == ["a", "b"]


def test_split_paragraphs_header_starts_own_paragraph() -> None:
    assert split_paragraphs("intro\n\n# Title\nbody line") == ["intro", "# Title", "body line"]


def test_split_paragraphs_bullet_line_own_paragraph() -> None:
    assert split_paragraphs("x\n\n- item") == ["x", "- item"]


def test_split_paragraphs_ordered_list_own_paragraph() -> None:
    assert split_paragraphs("x\n\n1. first") == ["x", "1. first"]


def test_split_paragraphs_soft_newline_preserved_inside_slot() -> None:
    assert split_paragraphs("line one\nline two") == ["line one\nline two"]


def test_join_split_roundtrip_for_plain_blocks() -> None:
    text = "first block\n\nsecond block"
    assert join_paragraphs(split_paragraphs(text)) == text


def test_replace_paragraph_at_index_updates_slot() -> None:
    text = "a\n\nb\n\nc"
    assert replace_paragraph_at_index(text, 1, "B") == "a\n\nB\n\nc"


def test_replace_paragraph_at_index_out_of_bounds_unchanged() -> None:
    text = "a\n\nb"
    assert replace_paragraph_at_index(text, 99, "x") == text
    assert replace_paragraph_at_index(text, -1, "x") == text


@pytest.mark.parametrize(
    ("text", "offset", "expected"),
    [
        ("a\n\nb", 0, 0),
        ("a\n\nb", 1, 0),
        ("a\n\nb", 2, 0),
        ("a\n\nb", 3, 1),
        ("a\n\nb", 4, 1),
        ("", 0, 0),
    ],
)
def test_paragraph_index_at_offset(text: str, offset: int, expected: int) -> None:
    assert paragraph_index_at_offset(text, offset) == expected


def test_wrapped_line_count_single_short_line_one_row() -> None:
    assert wrapped_line_count("hello", content_width=500.0) == 1


def test_wrapped_line_count_long_line_multiple_rows() -> None:
    # cpl ~= int((500-16)/8.15) = 59 -> 120 chars -> 3 rows
    line = "x" * 120
    assert wrapped_line_count(line, content_width=500.0) == 3


def test_distribute_heights_scales_to_target() -> None:
    out = distribute_heights([1.0, 1.0], target_total=100.0, min_slot=10.0)
    assert len(out) == 2
    assert pytest.approx(sum(out), rel=1e-3) == 100.0
    assert all(h >= 10.0 for h in out)
