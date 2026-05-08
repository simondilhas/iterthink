"""Focus-area action chrome: outer rail / hover wrapper around inner action grids."""

from __future__ import annotations

import flet as ft

from .constants import COMPARE_ACTION_COL_W


def wrap_workspace_action_chrome(inner: ft.Control, *, persistent: bool) -> tuple[ft.Container, ft.Container | None]:
    """Outer column width + top padding; optional hover fade wrapper (Compare row pattern)."""
    if persistent:
        actions_ctrl = ft.Container(
            content=inner,
            opacity=1.0,
            width=COMPARE_ACTION_COL_W,
            alignment=ft.Alignment.TOP_CENTER,
            padding=ft.padding.only(top=4),
        )
        return actions_ctrl, None
    hover_wrap = ft.Container(
        content=inner,
        opacity=0.0,
        animate_opacity=180,
        width=COMPARE_ACTION_COL_W,
        alignment=ft.Alignment.TOP_CENTER,
        padding=ft.padding.only(top=4),
    )
    return hover_wrap, hover_wrap
