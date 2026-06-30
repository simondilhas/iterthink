"""Tests for virtual compare ListView window math."""

from types import SimpleNamespace

import flet as ft

from iterthink.studio.history.compare_virtual import (
    COMPARE_VIRTUAL_MIN_ROWS,
    COMPARE_VIRTUAL_ROW_HEIGHT_PX,
    compare_build_row_heights,
    compare_estimated_row_height,
    compare_prefix_offsets,
    compare_row_scroll_ft_key,
    compare_row_scroll_key,
    compare_scroll_offset_for_row,
    compare_should_virtualize,
    compare_spacer_heights,
    compare_visible_window,
    compare_visible_window_for_heights,
    display_index_for_cand_idx,
)


def test_compare_row_scroll_key() -> None:
    assert compare_row_scroll_key(0) == "compare_para_0"
    assert compare_row_scroll_key(12) == "compare_para_12"


def test_compare_row_scroll_ft_key() -> None:
    key = compare_row_scroll_ft_key(3)
    assert isinstance(key, ft.ScrollKey)
    assert str(key) == "compare_para_3"


def test_compare_should_virtualize_disabled() -> None:
    assert not compare_should_virtualize(0)
    assert not compare_should_virtualize(COMPARE_VIRTUAL_MIN_ROWS - 1)
    assert not compare_should_virtualize(COMPARE_VIRTUAL_MIN_ROWS)
    assert not compare_should_virtualize(1000)


def test_full_rebuild_scroll_metadata_for_jump() -> None:
    """Mirror paragraph_ui post-rebuild scroll fields used by KI Comments jump."""
    display_rows = [
        SimpleNamespace(
            row_type="comparison",
            old_text="a",
            new_text="b",
            new_paragraph_index=0,
        ),
        SimpleNamespace(row_type="removed", old_text="x", new_text="", new_paragraph_index=-1),
        SimpleNamespace(
            row_type="comparison",
            old_text="c",
            new_text="d",
            new_paragraph_index=1,
        ),
    ]
    heights = compare_build_row_heights(display_rows, content_width=300.0, text_single=False)
    assert len(heights) == len(display_rows)
    assert all(h > 0 for h in heights)
    comp_by_display: list[int | None] = []
    comp = 0
    for row in display_rows:
        if row.row_type in ("ghost_moved", "removed"):
            comp_by_display.append(None)
        else:
            comp_by_display.append(comp)
            comp += 1
    comp_display_index = {
        int(ci): di for di, ci in enumerate(comp_by_display) if ci is not None
    }
    display_idx = display_index_for_cand_idx(
        1,
        eval_cand_indices=[0, 1],
        comp_display_index=comp_display_index,
        display_rows=display_rows,
    )
    assert display_idx == 2
    assert compare_scroll_offset_for_row(display_idx, heights, margin=0.0) == sum(heights[:2])


def test_compare_visible_window_bounds() -> None:
    first, last = compare_visible_window(
        scroll_offset=0.0,
        viewport_height=400.0,
        total_rows=100,
        row_height=50.0,
        overscan=2,
    )
    assert first == 0
    assert last > first
    assert last <= 100

    first2, last2 = compare_visible_window(
        scroll_offset=500.0,
        viewport_height=400.0,
        total_rows=100,
        row_height=50.0,
        overscan=2,
    )
    assert first2 >= 8
    assert last2 <= 100
    assert last2 > first2


def test_compare_estimated_row_height_scales_with_lines() -> None:
    short = compare_estimated_row_height("one line", "one line", content_width=400.0)
    long = compare_estimated_row_height("word " * 200, "word " * 200, content_width=120.0)
    assert long > short
    assert short >= COMPARE_VIRTUAL_ROW_HEIGHT_PX


def test_compare_visible_window_for_heights_near_document_end() -> None:
    heights = [52.0] * 38 + [180.0, 200.0, 220.0, 240.0]
    prefix = compare_prefix_offsets(heights)
    total = prefix[-1]
    viewport = 400.0
    scroll = max(0.0, total - viewport - 20.0)
    first, last = compare_visible_window_for_heights(
        scroll_offset=scroll,
        heights=heights,
        viewport_height=viewport,
        overscan=2,
    )
    assert last == len(heights)
    assert first < last
    top, tail = compare_spacer_heights(first, last, heights)
    assert top + tail + sum(heights[first:last]) <= total + 1e-6


def test_compare_scroll_offset_for_row_uses_prefix_sum() -> None:
    heights = [60.0, 80.0, 100.0, 120.0]
    assert compare_scroll_offset_for_row(2, heights, margin=0.0) == 140.0
    assert compare_scroll_offset_for_row(0, heights) == 0.0


def test_compare_build_row_heights_aligns_with_display_rows() -> None:
    rows = [
        SimpleNamespace(row_type="comparison", old_text="a", new_text="b"),
        SimpleNamespace(row_type="ghost_moved", old_text="ghost", new_text=""),
        SimpleNamespace(row_type="removed", old_text="gone", new_text=""),
    ]
    heights = compare_build_row_heights(rows, content_width=300.0, text_single=False)
    assert len(heights) == 3
    assert all(h >= COMPARE_VIRTUAL_ROW_HEIGHT_PX for h in heights)


def test_compare_visible_window_for_heights_tall_rows_mid_document() -> None:
    heights = [52.0, 52.0, 300.0, 52.0, 52.0]
    scroll = compare_prefix_offsets(heights)[2]
    first, last = compare_visible_window_for_heights(
        scroll_offset=scroll,
        heights=heights,
        viewport_height=200.0,
        overscan=1,
    )
    assert first <= 2
    assert last >= 3
    assert last > first
