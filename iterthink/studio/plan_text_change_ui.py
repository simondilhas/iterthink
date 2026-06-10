"""Plan text change overlay: label colors and desktop hover cards."""

from __future__ import annotations

import flet as ft

from iterthink import config
from iterthink.compare.diff_card import build_new_side_spans, build_old_side_spans
from iterthink.services.plan_text_diff import PlanTextChangeKind
from iterthink.studio import ui_theme

_HOVER_CARD_MAX_W = 360.0
_LABEL_BASE_SIZE = 11
_HEADER_SIZE = 10


def plan_hover_enabled(page: ft.Page | None) -> bool:
    if page is None:
        return True
    pl = getattr(page, "platform", None)
    if pl is None:
        return True
    name = str(pl).lower()
    return name not in ("ios", "android", "pageplatform.ios", "pageplatform.android")


def label_colors(kind: PlanTextChangeKind) -> tuple[str, str | None]:
    """Foreground and optional background for in-place plan text labels."""
    fg = ui_theme.editor_text_color()
    if kind == "stable":
        return fg, None
    if kind == "added":
        _bg, _pill_fg = ui_theme.compare_slot_pill_colors("added")
        return fg, _bg
    if kind == "modified":
        _bg, _pill_fg = ui_theme.compare_slot_pill_colors("modified")
        return fg, _bg
    return fg, None


def pin_color(kind: PlanTextChangeKind) -> str:
    if kind == "added":
        return config.SUCCESS
    if kind == "removed":
        return "#EF5350"
    return config.PRIMARY_COLOR


def _header(text: str) -> ft.Text:
    return ft.Text(
        text,
        size=_HEADER_SIZE,
        weight=ft.FontWeight.W_600,
        color=config.ON_SURFACE_VARIANT,
    )


def _body_spans(old_text: str, new_text: str, *, side: str) -> ft.Text:
    fg = ui_theme.editor_text_color()
    if side == "old":
        spans = build_old_side_spans(
            old_text,
            new_text,
            base_size=_LABEL_BASE_SIZE,
            base_color=fg,
        )
    else:
        spans = build_new_side_spans(
            old_text,
            new_text,
            base_size=_LABEL_BASE_SIZE,
            base_color=fg,
        )
    return ft.Text(spans=spans, size=_LABEL_BASE_SIZE, selectable=True)


def build_inline_label_text(
    kind: PlanTextChangeKind,
    display_text: str,
    old_text: str | None,
    new_text: str | None,
    *,
    font_size: int,
) -> ft.Text:
    """In-place plan label with word-level diff for modified/removed lines."""
    fg = ui_theme.editor_text_color()
    old_s = (old_text or "").strip()
    new_s = (new_text or display_text or "").strip()
    if kind == "modified" and old_s and new_s:
        spans = build_new_side_spans(
            old_s,
            new_s,
            base_size=font_size,
            base_color=fg,
        )
        return ft.Text(
            spans=spans,
            size=font_size,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=False,
        )
    if kind == "removed" and old_s:
        spans = build_old_side_spans(
            old_s,
            "",
            base_size=font_size,
            base_color=fg,
        )
        return ft.Text(
            spans=spans,
            size=font_size,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=False,
        )
    return ft.Text(
        display_text,
        size=font_size,
        color=fg,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
        no_wrap=False,
    )


def build_text_change_hover_card(
    old_text: str | None,
    new_text: str | None,
    *,
    kind: PlanTextChangeKind,
) -> ft.Container:
    """Was | Now side-by-side diff card for desktop hover."""
    old_s = (old_text or "").strip()
    new_s = (new_text or "").strip()
    cols: list[ft.Control] = []

    if kind == "added":
        cols.append(
            ft.Column(
                [_header("Now"), _body_spans("", new_s, side="new")],
                spacing=4,
                tight=True,
                expand=True,
            )
        )
    elif kind == "removed":
        cols.append(
            ft.Column(
                [_header("Was"), _body_spans(old_s, "", side="old")],
                spacing=4,
                tight=True,
                expand=True,
            )
        )
    else:
        cols.extend(
            [
                ft.Column(
                    [_header("Was"), _body_spans(old_s, new_s, side="old")],
                    spacing=4,
                    tight=True,
                    expand=True,
                ),
                ft.Container(width=1, height=40, bgcolor=ui_theme.outline_muted()),
                ft.Column(
                    [_header("Now"), _body_spans(old_s, new_s, side="new")],
                    spacing=4,
                    tight=True,
                    expand=True,
                ),
            ]
        )

    return ft.Container(
        content=ft.Row(cols, spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
        width=_HOVER_CARD_MAX_W,
        bgcolor=config.SURFACE,
        border=ft.border.all(1, ui_theme.outline_muted()),
        border_radius=8,
        padding=10,
    )
