"""Reusable studio UI pieces: paragraph action grids (Compose / Compare), rail icon buttons."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from iterthink import config, ui_theme
from iterthink.studio_constants import (
    COMPARE_ACTION_GRID_CELL,
    COMPARE_ACTION_H_PAD,
    COMPARE_ACTION_INNER_W,
    COMPARE_ACTION_V_PAD,
    PROJECT_PAGE_TOOLTIP,
)

# --- Compare / Compose action rail (icon buttons) ---

ACTION_RAIL_ICON_SIZE = 14

ACTION_APPROVE_PARAGRAPH_TOOLTIP = "Apply this paragraph to the document"
ACTION_REJECT_PARAGRAPH_TOOLTIP = "Reset this paragraph"


def action_rail_icon_button_style() -> ft.ButtonStyle:
    """Compact chrome shared by play / approve / reject in paragraph action cells."""
    return ft.ButtonStyle(
        padding=ft.padding.symmetric(horizontal=2, vertical=1),
        visual_density=ft.VisualDensity.COMPACT,
    )


def action_rail_play_icon_button(
    *,
    on_click: Callable[[ft.ControlEvent], Any],
    tooltip: str | None = None,
    icon_size: int = ACTION_RAIL_ICON_SIZE,
) -> ft.IconButton:
    return ft.IconButton(
        ft.Icons.PLAY_ARROW,
        icon_size=icon_size,
        icon_color=config.PRIMARY_COLOR,
        tooltip=tooltip or PROJECT_PAGE_TOOLTIP,
        style=action_rail_icon_button_style(),
        on_click=on_click,
    )


def action_rail_approve_icon_button(
    *,
    on_click: Callable[[ft.ControlEvent], Any],
    tooltip: str | None = None,
    icon_size: int = ACTION_RAIL_ICON_SIZE,
) -> ft.IconButton:
    return ft.IconButton(
        ft.Icons.CHECK_ROUNDED,
        icon_size=icon_size,
        icon_color=config.PRIMARY_COLOR,
        tooltip=tooltip or ACTION_APPROVE_PARAGRAPH_TOOLTIP,
        style=action_rail_icon_button_style(),
        on_click=on_click,
    )


def action_rail_reject_icon_button(
    *,
    on_click: Callable[[ft.ControlEvent], Any],
    tooltip: str | None = None,
    icon_size: int = ACTION_RAIL_ICON_SIZE,
) -> ft.IconButton:
    return ft.IconButton(
        ft.Icons.CLOSE_ROUNDED,
        icon_size=icon_size,
        icon_color=config.ON_SURFACE_VARIANT,
        tooltip=tooltip or ACTION_REJECT_PARAGRAPH_TOOLTIP,
        style=action_rail_icon_button_style(),
        on_click=on_click,
    )


# --- Margin “sparkle” (LLM prompts) popup ---

SPARKLE_MENU_ICON = ft.Icons.AUTO_AWESOME


def sparkle_margin_menu_chrome(
    *, for_compare: bool, compact: bool
) -> tuple[int, int | ft.Padding, ft.ButtonStyle, Any]:
    """Icon size, anchor padding, anchor button style, and menu icon color for margin sparkle popups."""
    icon_size = 14 if for_compare else (15 if compact else 18)
    pad: int | ft.Padding = (
        ft.padding.symmetric(horizontal=3, vertical=2)
        if for_compare
        else (2 if compact else 4)
    )
    style = ft.ButtonStyle(
        color=ft.Colors.with_opacity(0.5, config.ON_SURFACE),
        padding=(
            ft.padding.symmetric(horizontal=2, vertical=1)
            if for_compare
            else ft.padding.all(1 if compact else 2)
        ),
        visual_density=ft.VisualDensity.COMPACT,
    )
    icon_color = ft.Colors.with_opacity(0.85, config.PRIMARY_COLOR)
    return icon_size, pad, style, icon_color


def sparkle_margin_popup_menu(
    *,
    tooltip: str,
    items: list[ft.PopupMenuItem],
    for_compare: bool,
    compact: bool,
    on_menu_open: Callable[[], None] | None = None,
    on_menu_cancel: Callable[[], None] | None = None,
) -> ft.Control:
    """PopupMenuButton for margin prompts; Compose compact workspace cell gets a fixed half-width wrap."""
    icon_size, pad, style, icon_color = sparkle_margin_menu_chrome(for_compare=for_compare, compact=compact)
    pmb = ft.PopupMenuButton(
        icon=SPARKLE_MENU_ICON,
        icon_size=icon_size,
        icon_color=icon_color,
        tooltip=tooltip,
        padding=pad,
        style=style,
        menu_position=ft.PopupMenuPosition.UNDER,
        items=items,
        on_open=(lambda _e: on_menu_open()) if on_menu_open else None,
        on_cancel=(lambda _e: on_menu_cancel()) if on_menu_cancel else None,
    )
    inner: ft.Control
    if compact and not for_compare:
        cell_w = max(28, int(COMPARE_ACTION_INNER_W // 2))
        inner = ft.Container(
            width=cell_w,
            height=int(COMPARE_ACTION_GRID_CELL),
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=pmb,
        )
    else:
        inner = pmb
    if on_menu_open:
        return ft.GestureDetector(
            on_tap_down=lambda _e: on_menu_open(),
            content=inner,
        )
    return inner


def _action_grid_slot(content: ft.Control, *, row_h: float, expand: bool) -> ft.Container:
    """One cell: equal flex width when expand=True; content centered."""
    return ft.Container(
        expand=expand,
        height=row_h,
        alignment=ft.Alignment.CENTER,
        content=content,
    )


def build_action_square(
    *,
    left: ft.Control,
    right: ft.Control,
    row_h: float = COMPARE_ACTION_GRID_CELL,
) -> ft.Container:
    """Single-row 2×1 grid (e.g. workflow + sparkle on Compose). Same chrome as rectangle."""
    inner_w = COMPARE_ACTION_INNER_W
    row = ft.Row(
        [
            _action_grid_slot(left, row_h=row_h, expand=True),
            _action_grid_slot(right, row_h=row_h, expand=True),
        ],
        spacing=0,
    )
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.1, config.ON_SURFACE),
        border=ft.border.all(1, ui_theme.outline_muted(alpha=0.45)),
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=COMPARE_ACTION_H_PAD, vertical=COMPARE_ACTION_V_PAD),
        content=ft.Container(width=inner_w, content=row),
    )


def build_action_compose_vertical_strip(
    *,
    top: ft.Control,
    bottom: ft.Control,
    cell: float = COMPARE_ACTION_GRID_CELL,
) -> ft.Container:
    """Compose rail: play above sparkle, single column (narrower than compare 2×2 grid)."""
    cw = int(cell)
    ch = int(cell)

    def _slot(c: ft.Control) -> ft.Container:
        return ft.Container(width=cw, height=ch, alignment=ft.Alignment.CENTER, content=c)

    col = ft.Column(
        [_slot(top), _slot(bottom)],
        spacing=0,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.1, config.ON_SURFACE),
        border=ft.border.all(1, ui_theme.outline_muted(alpha=0.45)),
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=COMPARE_ACTION_H_PAD, vertical=COMPARE_ACTION_V_PAD),
        content=ft.Container(width=cw, height=2 * ch, content=col),
    )


def build_action_rectangle(
    *,
    top_left: ft.Control,
    top_right: ft.Control,
    bottom_left: ft.Control,
    bottom_right: ft.Control,
    row_h: float = COMPARE_ACTION_GRID_CELL,
) -> ft.Container:
    """2×2 grid (Compare / Review paragraph rows). Same outer chrome as build_action_square."""
    inner_w = COMPARE_ACTION_INNER_W
    col = ft.Column(
        [
            ft.Container(
                width=inner_w,
                content=ft.Row(
                    [
                        _action_grid_slot(top_left, row_h=row_h, expand=True),
                        _action_grid_slot(top_right, row_h=row_h, expand=True),
                    ],
                    spacing=0,
                ),
            ),
            ft.Container(
                width=inner_w,
                content=ft.Row(
                    [
                        _action_grid_slot(bottom_left, row_h=row_h, expand=True),
                        _action_grid_slot(bottom_right, row_h=row_h, expand=True),
                    ],
                    spacing=0,
                ),
            ),
        ],
        spacing=0,
        tight=True,
    )
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.1, config.ON_SURFACE),
        border=ft.border.all(1, ui_theme.outline_muted(alpha=0.45)),
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=COMPARE_ACTION_H_PAD, vertical=COMPARE_ACTION_V_PAD),
        content=ft.Container(width=inner_w, content=col),
    )
