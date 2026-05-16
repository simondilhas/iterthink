"""Focus-area action chrome: outer rail / hover wrapper around inner action grids."""

from __future__ import annotations

import flet as ft

from .constants import (
    COMPARE_ACTION_COL_W,
    COMPARE_ACTION_RAIL_CHROME_TOP_PAD,
    COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H,
)


def wrap_workspace_action_chrome(inner: ft.Control, *, persistent: bool) -> tuple[ft.Container, ft.Container | None]:
    """Outer column width + top padding; optional hover fade wrapper (Compare row pattern)."""
    if persistent:
        actions_ctrl = ft.Container(
            content=inner,
            opacity=1.0,
            width=COMPARE_ACTION_COL_W,
            height=float(COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H),
            alignment=ft.Alignment.TOP_CENTER,
            padding=ft.padding.only(top=COMPARE_ACTION_RAIL_CHROME_TOP_PAD),
        )
        return actions_ctrl, None
    hover_wrap = ft.Container(
        content=inner,
        opacity=0.0,
        animate_opacity=180,
        width=COMPARE_ACTION_COL_W,
        height=float(COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H),
        alignment=ft.Alignment.TOP_CENTER,
        padding=ft.padding.only(top=COMPARE_ACTION_RAIL_CHROME_TOP_PAD),
    )
    return hover_wrap, hover_wrap
