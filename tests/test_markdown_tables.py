"""Tests for GFM table preview parsing and column width math."""

from __future__ import annotations

from iterthink.studio.markdown_tables import (
    MdBlock,
    compute_column_widths_px,
    compute_proportional_widths,
    split_markdown_with_tables,
)


def test_split_markdown_with_tables_separates_table_and_paragraph() -> None:
    src = "Intro.\n\n| A | BBB |\n|---|---|\n| 1 | 22 |\n\nOutro."
    blocks = split_markdown_with_tables(src)
    assert len(blocks) == 3
    assert blocks[0].kind == "markdown"
    assert "Intro." in blocks[0].text
    assert blocks[0].text.endswith("\n\n")
    assert blocks[1].kind == "table"
    assert blocks[1].has_header is True
    assert blocks[1].rows == [["A", "BBB"], ["1", "22"]]
    assert blocks[2].kind == "markdown"
    assert blocks[2].text.lstrip("\n").startswith("Outro.")
    assert "Outro." in blocks[2].text


def test_split_markdown_without_tables_single_block() -> None:
    blocks = split_markdown_with_tables("Just text.\n")
    assert blocks == [MdBlock(kind="markdown", text="Just text.\n")]


def test_compute_proportional_widths_fills_avail() -> None:
    widths = compute_proportional_widths([40.0, 120.0, 60.0], 400.0, min_width=20.0)
    assert abs(sum(widths) - 400.0) < 0.01
    assert widths[1] > widths[0]
    assert widths[1] > widths[2]
    assert abs(widths[1] / widths[0] - 3.0) < 0.01


def test_compute_proportional_widths_scales_down_when_exceeding_avail() -> None:
    natural = [200.0, 200.0, 200.0]
    widths = compute_proportional_widths(natural, 300.0, min_width=40.0)
    assert abs(sum(widths) - 300.0) < 0.01
    assert abs(widths[0] - 100.0) < 0.01


def test_compute_column_widths_px_fills_avail() -> None:
    rows = [["A", "BBBBBBBB"], ["1", "2"]]
    widths = compute_column_widths_px(rows, avail_px=200.0, char_w=8.0, min_col_px=30.0, cell_pad_px=8.0)
    assert len(widths) == 2
    assert abs(sum(widths) - 200.0) < 0.01
    assert widths[1] > widths[0]
