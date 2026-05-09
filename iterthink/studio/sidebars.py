"""Left/right sidebar column layout and outer panel chrome."""

from __future__ import annotations

import flet as ft

from iterthink import config
from .constants import (
    SIDEBAR_INNER_BORDER_RADIUS_PX,
    SIDEBAR_INNER_PAD_PX,
    SIDEBAR_TOOLBAR_ROW_H_PX,
)
from .util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioSidebars:
    def toggle_left(self, _e: ft.ControlEvent | None = None) -> None:
        self.left_open = not self.left_open
        self.left_panel.content = self._build_left_column()
        self.reflow_columns()
        if _ctrl_on_page(self.left_panel):
            self.left_panel.update()

    def _explorer_collapse_handle_strip(self) -> ft.Control:
        """Right edge of tree card: hover pill; tap collapses."""
        return self._pane_split_handle(
            tooltip="Collapse explorer",
            on_toggle=self.toggle_left,
            strip_margin=ft.margin.only(left=-1),
        )

    def _build_left_column(self) -> ft.Control:
        if not self.left_open:
            return ft.Row(
                [
                    self._pane_split_handle(
                        tooltip="Show explorer",
                        on_toggle=self.toggle_left,
                        compact_rail=True,
                    ),
                ],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        return ft.Column(
            [
                ft.Container(
                    height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
                    content=ft.Row(
                        [
                            self._tree_search_bar,
                            ft.Row(
                                [
                                    self._tree_add_menu,
                                    self._tree_explorer_overflow_btn,
                                ],
                                tight=True,
                                spacing=0,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        expand=True,
                        spacing=8,
                        height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                ft.Row(
                    [
                        ft.Container(
                            content=self.tree_column,
                            expand=True,
                            padding=float(SIDEBAR_INNER_PAD_PX),
                            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
                            bgcolor=config.SURFACE,
                            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                        ),
                        self._explorer_collapse_handle_strip(),
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    spacing=0,
                ),
            ],
            expand=True,
            spacing=8,
        )

    def toggle_right(self, _e: ft.ControlEvent | None = None) -> None:
        self.right_open = not self.right_open
        self.right_panel.content = self._build_right_column()
        self.reflow_columns()
        if _ctrl_on_page(self.right_panel):
            self.right_panel.update()

    def _ki_rail_collapse_strip(self) -> ft.Control:
        """Left edge of KI card: hover pill; tap collapses (mirrors explorer handle)."""
        return self._pane_split_handle(
            tooltip="Collapse KI panel",
            on_toggle=self.toggle_right,
            strip_margin=ft.margin.only(right=-1),
        )

    def _build_right_column(self) -> ft.Control:
        if not self.right_open:
            return ft.Row(
                [
                    self._pane_split_handle(
                        tooltip="Show KI panel",
                        on_toggle=self.toggle_right,
                        compact_rail=True,
                    ),
                ],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        # Match left: fixed toolbar band, then one stretched row. Never set expand on the
        # spacer — expand=True would consume all vertical space and shrink the well.
        return ft.Column(
            [
                self._ki_topic_top_bar,
                ft.Row(
                    [
                        self._ki_rail_collapse_strip(),
                        ft.Container(
                            content=self._right_ki_column,
                            expand=True,
                        ),
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    spacing=0,
                ),
            ],
            expand=True,
            spacing=8,
        )

    def _sync_side_panel_chrome(self) -> None:
        """Expanded = rounded sidebar card; collapsed = square transparent rail."""
        if self.left_open:
            self.left_panel.border_radius = 15
            self.left_panel.padding = 8
            self.left_panel.bgcolor = config.SIDEBAR_SURFACE
        else:
            self.left_panel.border_radius = 0
            self.left_panel.padding = 0
            self.left_panel.bgcolor = ft.Colors.TRANSPARENT
        if self.right_open:
            self.right_panel.border_radius = 15
            self.right_panel.padding = 8
            self.right_panel.bgcolor = config.SIDEBAR_SURFACE
        else:
            self.right_panel.border_radius = 0
            self.right_panel.padding = 0
            self.right_panel.bgcolor = ft.Colors.TRANSPARENT
