"""Reusable Flet plan / PDF page strip (optional per-page zoom/pan)."""

from __future__ import annotations

from pathlib import Path

import flet as ft

# Viewport height per page so InteractiveViewer has a bounded area.
_DEFAULT_PAGE_VIEWPORT_H = 520.0


def plan_picture_column(
    page_png_paths: list[Path],
    *,
    page_viewport_height: float = _DEFAULT_PAGE_VIEWPORT_H,
    max_scale: float = 4.0,
    min_scale: float = 0.6,
    inner_scroll: bool = True,
) -> ft.Column:
    """
    Vertical strip of page images.

    With ``inner_scroll=True`` (default), each page uses ``InteractiveViewer`` for
    zoom/pan and the column scrolls itself.

    With ``inner_scroll=False``, each page is a plain ``Image`` so an outer
    ``ListView`` receives vertical wheel scroll (paired-column sync); zoom/pan per
    page is disabled in that mode.
    """
    rows: list[ft.Control] = []
    for i, p in enumerate(page_png_paths):
        img = ft.Image(
            src=str(p),
            fit=ft.BoxFit.CONTAIN,
            filter_quality=ft.FilterQuality.MEDIUM,
        )
        if inner_scroll:
            page_content: ft.Control = ft.InteractiveViewer(
                content=img,
                pan_enabled=True,
                scale_enabled=True,
                min_scale=min_scale,
                max_scale=max_scale,
                constrained=True,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
            )
        else:
            page_content = img
        rows.append(
            ft.Container(
                content=page_content,
                height=page_viewport_height,
                alignment=ft.Alignment.TOP_CENTER,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                border=ft.border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
                border_radius=8,
            )
        )
        if i + 1 < len(page_png_paths):
            rows.append(ft.Container(height=8))
    return ft.Column(
        rows,
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO if inner_scroll else None,
        expand=True,
    )


def plan_picture_single_viewport(
    page_png_paths: list[Path],
    *,
    page_index: int,
    page_viewport_height: float = _DEFAULT_PAGE_VIEWPORT_H,
    max_scale: float = 4.0,
    min_scale: float = 0.6,
) -> ft.Container:
    """One page by index; empty placeholder if index out of range."""
    if page_index < 0 or page_index >= len(page_png_paths):
        return ft.Container(
            height=page_viewport_height,
            alignment=ft.Alignment.CENTER,
            content=ft.Text("No page", color=ft.Colors.GREY_500),
        )
    p = page_png_paths[page_index]
    img = ft.Image(
        src=str(p),
        fit=ft.BoxFit.CONTAIN,
        filter_quality=ft.FilterQuality.MEDIUM,
    )
    viewer = ft.InteractiveViewer(
        content=img,
        pan_enabled=True,
        scale_enabled=True,
        min_scale=min_scale,
        max_scale=max_scale,
        constrained=True,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    return ft.Container(
        content=viewer,
        height=page_viewport_height,
        alignment=ft.Alignment.TOP_CENTER,
        border=ft.border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
        border_radius=8,
    )
