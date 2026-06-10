"""Tests for plan text change hover card UI."""

import flet as ft

from iterthink.studio.plan_text_change_ui import (
    build_inline_label_text,
    build_text_change_hover_card,
    label_colors,
    plan_hover_enabled,
)


def test_label_colors_stable_has_no_bg() -> None:
    fg, bg = label_colors("stable")
    assert fg
    assert bg is None


def test_label_colors_modified_has_bg() -> None:
    _fg, bg = label_colors("modified")
    assert bg is not None


def test_build_inline_label_modified_has_spans() -> None:
    txt = build_inline_label_text(
        "modified",
        "A-102",
        "A-101",
        "A-102",
        font_size=11,
    )
    assert isinstance(txt, ft.Text)
    assert txt.spans is not None
    assert len(txt.spans) >= 1


def test_build_inline_label_stable_plain() -> None:
    txt = build_inline_label_text(
        "stable",
        "Room 1",
        "Room 1",
        "Room 1",
        font_size=11,
    )
    assert isinstance(txt, ft.Text)
    assert txt.spans is None
    assert txt.value == "Room 1"


def test_build_hover_card_modified() -> None:
    card = build_text_change_hover_card("A-101", "A-102", kind="modified")
    assert isinstance(card, ft.Container)
    assert card.content is not None


def test_build_hover_card_added() -> None:
    card = build_text_change_hover_card(None, "New label", kind="added")
    assert isinstance(card, ft.Container)


def test_build_hover_card_removed() -> None:
    card = build_text_change_hover_card("Old label", None, kind="removed")
    assert isinstance(card, ft.Container)


def test_plan_hover_enabled_none_page() -> None:
    assert plan_hover_enabled(None) is True


def test_pin_color_kinds() -> None:
    from iterthink.studio.plan_text_change_ui import pin_color

    assert pin_color("added")
    assert pin_color("removed")
    assert pin_color("modified")
