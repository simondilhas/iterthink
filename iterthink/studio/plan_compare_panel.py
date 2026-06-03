"""Compare-tab controls for PDF baseline/candidate selection and diff overlay list."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import flet as ft

from iterthink import config
from . import ui_theme
from .util import ctrl_on_page


@dataclass
class PlanComparePanel:
    """Bar + scroll list for overlay PNGs; wiring stays in MarkdownStudio."""

    bar: ft.Row
    baseline_label: ft.Text
    candidate_label: ft.Text
    baseline_dd: ft.Dropdown
    candidate_dd: ft.Dropdown
    baseline_wrap: ft.Container
    candidate_wrap: ft.Container
    overlay_switch: ft.Switch
    side_by_side_switch: ft.Switch
    overlay_list: ft.ListView
    host: ft.Container

    def set_bar_visible(self, visible: bool) -> None:
        self.host.visible = visible
        if ctrl_on_page(self.host):
            self.host.update()


def build_plan_compare_panel(
    *,
    on_baseline: Callable[..., Any],
    on_candidate: Callable[..., Any],
    on_overlay: Callable[..., Any],
    on_side_by_side: Callable[..., Any] | None = None,
    on_hover_baseline: Callable[..., Any],
    on_hover_candidate: Callable[..., Any],
    on_baseline_focus: Callable[..., Any],
    on_baseline_blur: Callable[..., Any],
    on_candidate_focus: Callable[..., Any],
    on_candidate_blur: Callable[..., Any],
    dropdown_text_style: ft.TextStyle,
    menu_style: ft.MenuStyle,
    option_button_style: ft.ButtonStyle,
    label_text_style: ft.TextStyle,
    border_radius: float,
) -> PlanComparePanel:
    """Toolbar-style dropdowns (match History Older/Newer chrome)."""
    baseline_dd = ft.Dropdown(
        expand=True,
        dense=True,
        text_style=dropdown_text_style,
        filled=False,
        bgcolor=config.SURFACE,
        border=ft.InputBorder.NONE,
        border_width=0,
        content_padding=ft.padding.symmetric(horizontal=6, vertical=0),
        menu_style=menu_style,
        options=[],
        disabled=True,
        tooltip="Pick baseline PDF for plan overlay.",
        on_select=on_baseline,
        on_focus=on_baseline_focus,
        on_blur=on_baseline_blur,
    )
    baseline_wrap = ft.Container(
        content=baseline_dd,
        on_hover=on_hover_baseline,
        expand=True,
        bgcolor=config.SURFACE,
        border_radius=float(border_radius),
        border=ft.Border.all(1, ui_theme.outline_muted()),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        alignment=ft.Alignment.CENTER_LEFT,
        padding=ft.padding.symmetric(horizontal=2, vertical=1),
    )
    candidate_dd = ft.Dropdown(
        expand=True,
        dense=True,
        text_style=dropdown_text_style,
        filled=False,
        bgcolor=config.SURFACE,
        border=ft.InputBorder.NONE,
        border_width=0,
        content_padding=ft.padding.symmetric(horizontal=6, vertical=0),
        menu_style=menu_style,
        options=[],
        disabled=True,
        tooltip="Pick candidate PDF for plan overlay.",
        on_select=on_candidate,
        on_focus=on_candidate_focus,
        on_blur=on_candidate_blur,
    )
    candidate_wrap = ft.Container(
        content=candidate_dd,
        on_hover=on_hover_candidate,
        expand=True,
        bgcolor=config.SURFACE,
        border_radius=float(border_radius),
        border=ft.Border.all(1, ui_theme.outline_muted()),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        alignment=ft.Alignment.CENTER_LEFT,
        padding=ft.padding.symmetric(horizontal=2, vertical=1),
    )
    overlay_sw = ft.Switch(
        label="Show extracted text",
        value=False,
        disabled=True,
        visible=False,
        on_change=on_overlay,
    )
    side_sw = ft.Switch(
        label="Side-by-side",
        value=False,
        disabled=True,
        on_change=on_side_by_side or on_overlay,
    )
    overlay_list = ft.ListView(
        expand=True,
        spacing=8,
        padding=ft.padding.all(8),
        visible=False,
    )
    baseline_label = ft.Text("Baseline:", style=label_text_style)
    candidate_label = ft.Text("Compare:", style=label_text_style)
    bar = ft.Row(
        [
            ft.Container(
                expand=1,
                content=ft.Row(
                    [
                        baseline_label,
                        baseline_wrap,
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
            ),
            ft.Container(
                expand=1,
                content=ft.Row(
                    [
                        candidate_label,
                        candidate_wrap,
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
            ),
            overlay_sw,
            side_sw,
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )
    host = ft.Container(content=bar, visible=False, padding=ft.padding.only(top=2, bottom=0))
    return PlanComparePanel(
        bar=bar,
        baseline_label=baseline_label,
        candidate_label=candidate_label,
        baseline_dd=baseline_dd,
        candidate_dd=candidate_dd,
        baseline_wrap=baseline_wrap,
        candidate_wrap=candidate_wrap,
        overlay_switch=overlay_sw,
        side_by_side_switch=side_sw,
        overlay_list=overlay_list,
        host=host,
    )


def fill_pdf_dropdowns(
    baseline_dd: ft.Dropdown,
    candidate_dd: ft.Dropdown,
    options: list[tuple[str, str]],
    *,
    option_button_style: ft.ButtonStyle | None = None,
) -> None:
    """Options are (version_id str, label); newest-first lists → baseline older, candidate newer."""
    st = option_button_style
    opts = [
        ft.dropdown.Option(key=k, text=t, style=st) if st is not None else ft.dropdown.Option(key=k, text=t)
        for k, t in options
    ]
    baseline_dd.options = opts
    candidate_dd.options = list(opts)
    if not opts:
        baseline_dd.value = None
        candidate_dd.value = None
        return
    if len(opts) >= 2:
        baseline_dd.value = opts[1].key
        candidate_dd.value = opts[0].key
    else:
        baseline_dd.value = opts[0].key
        candidate_dd.value = opts[0].key


def populate_overlay_list(overlay_list: ft.ListView, paths: list[Path]) -> None:
    overlay_list.controls.clear()
    for p in paths:
        overlay_list.controls.append(
            ft.Container(
                content=ft.Image(src=str(p), fit=ft.BoxFit.CONTAIN),
                alignment=ft.Alignment.TOP_CENTER,
            )
        )
