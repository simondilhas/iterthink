"""Compare-tab controls for PDF baseline/candidate selection and diff overlay list."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import flet as ft


@dataclass
class PlanComparePanel:
    """Bar + scroll list for overlay PNGs; wiring stays in MarkdownStudio."""

    bar: ft.Row
    baseline_dd: ft.Dropdown
    candidate_dd: ft.Dropdown
    overlay_switch: ft.Switch
    overlay_list: ft.ListView
    host: ft.Container

    def set_bar_visible(self, visible: bool) -> None:
        self.host.visible = visible
        if self.host.page:
            self.host.update()


def build_plan_compare_panel(
    *,
    on_baseline,
    on_candidate,
    on_overlay,
) -> PlanComparePanel:
    baseline_dd = ft.Dropdown(
        width=240,
        dense=True,
        text_size=13,
        label="PDF baseline",
        disabled=True,
        options=[],
        on_select=on_baseline,
    )
    candidate_dd = ft.Dropdown(
        width=240,
        dense=True,
        text_size=13,
        label="PDF compare",
        disabled=True,
        options=[],
        on_select=on_candidate,
    )
    overlay_sw = ft.Switch(
        label="Diff overlay",
        value=False,
        disabled=True,
        on_change=on_overlay,
    )
    overlay_list = ft.ListView(
        expand=True,
        spacing=8,
        padding=ft.padding.all(8),
        visible=False,
    )
    bar = ft.Row(
        [
            baseline_dd,
            candidate_dd,
            overlay_sw,
        ],
        tight=True,
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    host = ft.Container(content=bar, visible=False, padding=ft.padding.only(bottom=6))
    return PlanComparePanel(
        bar=bar,
        baseline_dd=baseline_dd,
        candidate_dd=candidate_dd,
        overlay_switch=overlay_sw,
        overlay_list=overlay_list,
        host=host,
    )


def fill_pdf_dropdowns(
    baseline_dd: ft.Dropdown,
    candidate_dd: ft.Dropdown,
    options: list[tuple[str, str]],
) -> None:
    """Options are (version_id str, label); newest-first lists → baseline older, candidate newer."""
    opts = [ft.dropdown.Option(key=k, text=t) for k, t in options]
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
                content=ft.Image(src=str(p), fit=ft.ImageFit.CONTAIN),
                alignment=ft.Alignment.TOP_CENTER,
            )
        )
