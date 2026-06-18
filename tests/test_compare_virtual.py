"""Tests for virtual compare ListView window math."""

from iterthink.studio.history.compare_virtual import (
    COMPARE_VIRTUAL_MIN_ROWS,
    compare_should_virtualize,
    compare_visible_window,
)


def test_compare_should_virtualize_threshold() -> None:
    assert not compare_should_virtualize(COMPARE_VIRTUAL_MIN_ROWS - 1)
    assert compare_should_virtualize(COMPARE_VIRTUAL_MIN_ROWS)


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
