"""Tests for Review Analyse result-card positioning helpers."""

from types import SimpleNamespace

import flet as ft

from iterthink.studio.constants import COMPARE_EVAL_COL_W, RESULT_CARD_MAX_H
from iterthink.studio.history.compare_virtual import (
    COMPARE_VIRTUAL_ROW_HEIGHT_PX,
    build_future_comp_display_index,
    listview_y_before_control,
    result_card_left_beside_eval,
    result_card_top_beside_row,
    result_card_top_for_row,
)


def test_result_card_left_beside_eval() -> None:
    assert result_card_left_beside_eval(COMPARE_EVAL_COL_W) == float(COMPARE_EVAL_COL_W) + 4.0


def test_listview_y_before_control_sums_explicit_heights() -> None:
    a = ft.Container(height=40)
    b = ft.Container(height=80)
    c = ft.Container()
    controls = [a, b, c]
    assert listview_y_before_control(controls, c, {}) == 120.0


def test_result_card_top_beside_row_uses_measured_offset() -> None:
    top = result_card_top_beside_row(
        row_y=240.0,
        scroll_offset=100.0,
        list_padding_top=2.0,
        stack_height=640.0,
        card_max_height=float(RESULT_CARD_MAX_H),
    )
    assert top == 142.0


def test_result_card_top_for_row_at_scroll_origin() -> None:
    top = result_card_top_for_row(
        display_index=0,
        scroll_offset=0.0,
        stack_height=640.0,
        card_max_height=float(RESULT_CARD_MAX_H),
    )
    assert top == 4.0


def test_result_card_top_clamps_at_bottom() -> None:
    top = result_card_top_for_row(
        display_index=100,
        scroll_offset=0.0,
        stack_height=400.0,
        card_max_height=float(RESULT_CARD_MAX_H),
    )
    assert top == 400.0 - float(RESULT_CARD_MAX_H) - 4.0


def test_result_card_top_scrolled_row_near_viewport_top() -> None:
    display_index = 50
    scroll = display_index * COMPARE_VIRTUAL_ROW_HEIGHT_PX
    top = result_card_top_for_row(
        display_index=display_index,
        scroll_offset=scroll,
        stack_height=640.0,
        card_max_height=float(RESULT_CARD_MAX_H),
    )
    assert 4.0 <= top <= 60.0


def test_result_card_top_not_old_index_times_88() -> None:
    display_index = 50
    scroll = 2400.0
    top = result_card_top_for_row(
        display_index=display_index,
        scroll_offset=scroll,
        stack_height=640.0,
        card_max_height=float(RESULT_CARD_MAX_H),
    )
    old_broken_top = display_index * 88.0 + 4.0
    assert top < 300.0
    assert top < old_broken_top / 10


def test_build_future_comp_display_index_skips_ghosts_in_text_single() -> None:
    rows = [
        SimpleNamespace(row_type="comparison"),
        SimpleNamespace(row_type="ghost_moved"),
        SimpleNamespace(row_type="comparison"),
    ]
    mapping = build_future_comp_display_index(rows, text_single=True)
    assert mapping == {0: 0, 1: 2}
