
"""Window chrome: menu bar, CSD header, explorer column, build()."""

from __future__ import annotations

import asyncio
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

import flet as ft
from flet.controls.types import PagePlatform

from iterthink import config

from . import ui_theme
from iterthink import licensing

from . import settings_ui
from .constants import (
    PANE_HANDLE_HEIGHT_PX,
    PANE_HANDLE_STRIP_W_PX,
    PANE_HANDLE_WIDTH_PX,
)
from .markdown_preview import markdown_preview_with_task_checkboxes
from .util import ctrl_on_page as _ctrl_on_page

_HELP_MD_PATH = Path(__file__).resolve().parent.parent / "help.md"
_LICENSE_PATH = Path(__file__).resolve().parent / "LICENSE"


def _read_bundled_license_text() -> str:
    try:
        return _LICENSE_PATH.read_text(encoding="utf-8")
    except OSError:
        return (
            "Business Source License 1.1 — full text not found next to the package.\n\n"
            "See the LICENSE file in the source repository."
        )


class MarkdownStudioShell:
    def _invalidate_header_hide(self) -> None:
        self._header_hide_gen += 1

    def _on_top_menu_open(self, _e: ft.ControlEvent | None = None) -> None:
        self._header_menu_open += 1
        self._invalidate_header_hide()

    def _on_top_menu_close(self, _e: ft.ControlEvent | None = None) -> None:
        self._header_menu_open = max(0, self._header_menu_open - 1)
        if self._header_menu_open == 0 and not self._header_chrome_hover:
            self._schedule_header_hide()

    def _schedule_header_hide(self) -> None:
        self._header_hide_gen += 1
        token = self._header_hide_gen
        self.page.run_task(self._hide_header_if_stale, token)

    async def _hide_header_if_stale(self, token: int) -> None:
        await asyncio.sleep(0.12)
        if token != self._header_hide_gen:
            return
        self._collapse_header_bar()

    def _collapse_header_bar(self) -> None:
        sh = self._header_shell
        if not sh:
            return
        sh.height = 0
        sh.opacity = 0.0
        sh.ignore_interactions = True
        sh.clip_behavior = ft.ClipBehavior.HARD_EDGE
        self._header_menu_open = 0
        if _ctrl_on_page(sh):
            sh.update()

    def _expand_header_bar(self) -> None:
        self._invalidate_header_hide()
        sh = self._header_shell
        if not sh:
            return
        sh.height = 50
        sh.opacity = 1.0
        sh.ignore_interactions = False
        # Flet default is HARD_EDGE; clipping can hide the open menu panel.
        sh.clip_behavior = ft.ClipBehavior.NONE
        if _ctrl_on_page(sh):
            sh.update()

    def _on_header_strip_hover(self, e: ft.ControlEvent) -> None:
        if self.page.web or self._header_shell is None:
            return
        if e.data:
            self._expand_header_bar()
        elif self._header_menu_open == 0:
            self._schedule_header_hide()

    def _on_header_chrome_hover(self, e: ft.ControlEvent) -> None:
        if self.page.web or self._header_shell is None:
            return
        self._header_chrome_hover = bool(e.data)
        if e.data:
            self._expand_header_bar()
        elif self._header_menu_open == 0:
            self._schedule_header_hide()

    def _use_csd(self) -> bool:
        if self.page.web:
            return False
        pl = getattr(self.page, "platform", None)
        if pl in (PagePlatform.LINUX, PagePlatform.WINDOWS):
            return True
        if pl is None:
            return sys.platform.startswith("linux") or sys.platform == "win32"
        return False

    def _win_minimize(self, _e: ft.ControlEvent | None = None) -> None:
        self.page.window.minimized = True
        self.page.update()

    def _win_toggle_max(self, _e: ft.ControlEvent | None = None) -> None:
        self.page.window.maximized = not bool(self.page.window.maximized)
        self.page.update()

    async def _win_close_async(self, _e: ft.ControlEvent | None = None) -> None:
        await self.page.window.close()

    def _close_about_open_license(self, _e: ft.ControlEvent | None = None) -> None:
        self.page.pop_dialog()
        self._open_license_dialog()

    def _open_license_dialog(self, _e: ft.ControlEvent | None = None) -> None:
        body = ft.Text(
            _read_bundled_license_text(),
            selectable=True,
            font_family="monospace",
            size=12,
        )
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("License — BUSL-1.1", weight=ft.FontWeight.W_600),
                content=ft.Container(
                    content=ft.Column([body], scroll=ft.ScrollMode.AUTO),
                    width=min(640, max(400, int(self.page.width * 0.85)) if self.page.width else 560),
                    height=min(520, max(320, int(self.page.height * 0.65)) if self.page.height else 480),
                ),
                actions=[ft.TextButton("Close", on_click=lambda e: self.page.pop_dialog())],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _open_pricing_page_async(self) -> None:
        url = licensing.PRICING_URL
        try:
            await self.page.launch_url(url)
        except Exception:
            webbrowser.open(url)

    def _open_pricing_page(self, _e: ft.ControlEvent | None = None) -> None:
        self.page.run_task(self._open_pricing_page_async)

    def _build_license_banner(self) -> ft.Control:
        if licensing.is_licensed():
            return ft.Container()
        pill = ft.Container(
            content=ft.Text(
                "Free for personal use — Get Commercial License",
                size=12,
                weight=ft.FontWeight.W_500,
                color=config.PRIMARY_COLOR,
            ),
            padding=ft.padding.symmetric(horizontal=10, vertical=4),
            border_radius=12,
            bgcolor=ft.Colors.with_opacity(0.10, config.PRIMARY_COLOR),
        )
        return ft.TextButton(
            content=pill,
            style=ft.ButtonStyle(
                padding=ft.padding.all(0),
                overlay_color=ft.Colors.with_opacity(0.08, config.PRIMARY_COLOR),
            ),
            on_click=self._open_pricing_page,
        )

    def _refresh_license_banner(self) -> None:
        host = self._license_banner_host
        if host is None:
            return
        host.content = self._build_license_banner()
        if _ctrl_on_page(host):
            host.update()

    def _open_about(self, _e: ft.ControlEvent | None = None) -> None:
        _rule = ui_theme.outline_muted(alpha=0.38)

        def _h_rule() -> ft.Container:
            return ft.Container(
                height=1,
                margin=ft.margin.symmetric(vertical=16),
                bgcolor=_rule,
            )

        _body_lo = ft.TextStyle(
            size=14,
            height=1.55,
            weight=ft.FontWeight.W_400,
            color=config.ON_SURFACE,
        )
        _section_hd = ft.TextStyle(
            size=14,
            height=1.4,
            weight=ft.FontWeight.W_600,
            color=config.ON_SURFACE,
        )

        license_link_btn = ft.TextButton(
            "[View BUSL-1.1 License]",
            style=ft.ButtonStyle(
                color=config.PRIMARY_COLOR,
                padding=ft.Padding.symmetric(horizontal=0, vertical=0),
                visual_density=ft.VisualDensity.COMPACT,
            ),
            on_click=self._close_about_open_license,
        )

        content_children: list[ft.Control] = [
            ft.Text(
                "Iterthink",
                size=28,
                weight=ft.FontWeight.W_700,
                color=config.ON_SURFACE,
            ),
            ft.Container(height=10),
            ft.Text(
                "See every change. Understand the impact.",
                size=16,
                height=1.45,
                weight=ft.FontWeight.W_600,
                color=config.ON_SURFACE,
            ),
            ft.Container(height=8),
            ft.Text(
                "Private by default, not by policy.",
                size=15,
                height=1.5,
                weight=ft.FontWeight.W_400,
                italic=True,
                color=config.ON_SURFACE_VARIANT,
            ),
            _h_rule(),
            ft.Text("Local AI Intelligence", style=_section_hd),
            ft.Container(height=10),
            ft.Text(
                'Powered by Ollama to help you iterate without the "final_v7" mess. '
                "Your drafts, your models, your machine.",
                style=_body_lo,
            ),
            _h_rule(),
            ft.Text("Legal & Version", style=_section_hd),
            ft.Container(height=10),
            ft.Text("© 2026 abstract ltd, Basel.", style=_body_lo),
            ft.Container(height=8),
            ft.Row(
                [
                    ft.Text("Version 1.0.4 • ", style=_body_lo),
                    license_link_btn,
                ],
                tight=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
            ),
            _h_rule(),
        ]

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(
                    "About",
                    size=13,
                    weight=ft.FontWeight.W_500,
                    color=config.ON_SURFACE_VARIANT,
                ),
                content=ft.Container(
                    width=440,
                    padding=ft.padding.only(top=4, bottom=8, left=4, right=4),
                    content=ft.Column(
                        content_children,
                        spacing=0,
                        tight=True,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                    ),
                ),
                actions=[ft.TextButton("Close", on_click=lambda e: self.page.pop_dialog())],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _menu_bar_style(self) -> ft.MenuStyle:
        """MenuBar defaults draw an outline; omit it so the header stays flat."""
        return ft.MenuStyle(
            bgcolor=config.SURFACE_VARIANT,
            side=ft.BorderSide(style=ft.BorderStyle.NONE),
            elevation=0,
        )

    def _submenu_bar_button_style(self) -> ft.ButtonStyle:
        """Top-level File / View / Help: no outline box around each label."""
        none_side = ft.BorderSide(style=ft.BorderStyle.NONE)
        return ft.ButtonStyle(
            side=none_side,
            color=config.ON_SURFACE,
            overlay_color=ft.Colors.with_opacity(0.08, config.ON_SURFACE),
        )

    def _submenu_dropdown_style(self) -> ft.MenuStyle:
        return ft.MenuStyle(
            bgcolor=config.SURFACE_VARIANT,
            side=ft.BorderSide(style=ft.BorderStyle.NONE),
            elevation=0,
        )

    def _open_help(self, _e: ft.ControlEvent | None = None) -> None:
        try:
            md_source = _HELP_MD_PATH.read_text(encoding="utf-8")
        except OSError:
            md_source = (
                "# Help file missing\n\n"
                f"Expected `{_HELP_MD_PATH}` — reinstall or restore **help.md** next to the app package."
            )
        md_view = ft.Markdown(
            value=markdown_preview_with_task_checkboxes(md_source),
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
            soft_line_break=True,
            md_style_sheet=ui_theme.compose_preview_markdown_style_sheet(),
        )
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Help", weight=ft.FontWeight.W_600),
                content=ft.Container(
                    content=ft.Column([md_view], scroll=ft.ScrollMode.AUTO),
                    width=min(640, max(400, int(self.page.width * 0.85)) if self.page.width else 560),
                    height=min(520, max(320, int(self.page.height * 0.65)) if self.page.height else 480),
                ),
                actions=[ft.TextButton("Close", on_click=lambda e: self.page.pop_dialog())],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _build_menu_bar(self) -> ft.MenuBar:
        bar_btn = self._submenu_bar_button_style()
        drop = self._submenu_dropdown_style()

        def _top_submenu(label: str, items: list[ft.Control]) -> ft.SubmenuButton:
            return ft.SubmenuButton(
                content=ft.Text(label, color=config.ON_SURFACE, size=14),
                style=bar_btn,
                menu_style=drop,
                alignment_offset=ft.Offset(0, 0),
                # Flet default is HARD_EDGE; clipping can hide the open menu panel.
                clip_behavior=ft.ClipBehavior.NONE,
                on_open=self._on_top_menu_open,
                on_close=self._on_top_menu_close,
                controls=items,
            )

        return ft.MenuBar(
            expand=False,
            style=self._menu_bar_style(),
            controls=[
                _top_submenu(
                    "File",
                    [
                        ft.MenuItemButton(
                            content=ft.Text("Save (Ctrl+S)", color=config.ON_SURFACE, size=14),
                            on_click=lambda e: self.page.run_task(self.save_file, e),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Export…", color=config.ON_SURFACE, size=14),
                            on_click=lambda e: self.page.run_task(self.begin_export_to_word, None),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Settings…", color=config.ON_SURFACE, size=14),
                            on_click=lambda e: self.page.run_task(settings_ui.open_settings_dialog, self),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Quit", color=config.ON_SURFACE, size=14),
                            on_click=lambda e: self.page.run_task(self._win_close_async, e),
                        ),
                    ],
                ),
                _top_submenu(
                    "View",
                    [
                        ft.MenuItemButton(
                            content=ft.Text("Toggle explorer", color=config.ON_SURFACE, size=14),
                            on_click=lambda e: self.toggle_left(e),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Toggle KI panel", color=config.ON_SURFACE, size=14),
                            on_click=lambda e: self.toggle_right(e),
                        ),
                    ],
                ),
                _top_submenu(
                    "Help",
                    [
                        ft.MenuItemButton(
                            content=ft.Text("Help…", color=config.ON_SURFACE, size=14),
                            on_click=self._open_help,
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("License…", color=config.ON_SURFACE, size=14),
                            on_click=self._open_license_dialog,
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("About", color=config.ON_SURFACE, size=14),
                            on_click=self._open_about,
                        ),
                    ],
                ),
            ],
        )

    def _rebuild_header_menu_bar(self) -> None:
        """Replace File/View/Help so text and dropdown surfaces match ``config`` (e.g. after appearance change)."""
        if self.page.web or self._header_shell is None:
            return
        row = self._header_shell.content
        if not isinstance(row, ft.Row) or not row.controls:
            return
        new_bar = self._build_menu_bar()
        self._menu_bar = new_bar
        row.controls[0] = new_bar

    def _pane_split_handle(
        self,
        *,
        tooltip: str,
        on_toggle: Callable[[ft.ControlEvent | None], None],
        compact_rail: bool = False,
        strip_margin: ft.Margin | None = None,
    ) -> ft.Control:
        """Collapsed rail: wide strip + light pill. Expanded: SURFACE bar, blue on hover."""
        if compact_rail:
            pill_idle = ft.Colors.with_opacity(0.28, config.ON_SURFACE)
            pill = ft.Container(
                width=float(PANE_HANDLE_WIDTH_PX),
                height=float(PANE_HANDLE_HEIGHT_PX),
                bgcolor=pill_idle,
            )

            def _on_strip_hover(e: ft.ControlEvent) -> None:
                pill.bgcolor = config.PRIMARY_COLOR if e.data else pill_idle
                if _ctrl_on_page(pill):
                    pill.update()

            strip = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                tooltip=tooltip,
                content=pill,
                on_hover=_on_strip_hover,
            )
        else:
            bar = ft.Container(
                width=float(PANE_HANDLE_WIDTH_PX),
                height=float(PANE_HANDLE_HEIGHT_PX),
                bgcolor=config.SURFACE,
            )

            def _on_strip_hover_exp(e: ft.ControlEvent) -> None:
                bar.bgcolor = config.PRIMARY_COLOR if e.data else config.SURFACE
                if _ctrl_on_page(bar):
                    bar.update()

            strip = ft.Container(
                width=float(PANE_HANDLE_STRIP_W_PX),
                expand=True,
                alignment=ft.Alignment.CENTER,
                tooltip=tooltip,
                margin=strip_margin,
                content=bar,
                on_hover=_on_strip_hover_exp,
            )
        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=lambda _e: on_toggle(None),
            content=strip,
            expand=compact_rail,
        )

    def build(self) -> ft.Control:
        self.ensure_file_pickers()
        self._rebuild_tree_ui()
        self.left_panel.content = self._build_left_column()

        toolbar = ft.Container(
            content=ft.Row(
                [
                    ft.Container(expand=True),
                    self.title_hit,
                    ft.Container(expand=True),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(bottom=4),
        )

        center_children: list[ft.Control] = []
        if not self._use_csd():
            center_children.append(toolbar)
        center_children.append(self.sheet_scroll)
        self._center_editor_column = ft.Column(
            center_children,
            expand=True,
            spacing=4,
        )
        self.center_panel.content = self._center_editor_column

        self.right_panel.content = self._build_right_column()

        self._main_row = ft.Row(
            [self.left_panel, self.center_panel, self.right_panel],
            expand=True,
            spacing=20,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        self._header_shell = None
        main_column_children: list[ft.Control] = []

        if not self.page.web:
            menu = self._build_menu_bar()
            self._menu_bar = menu
            self._license_banner_host = ft.Container(
                content=self._build_license_banner(),
                padding=ft.padding.only(right=12),
            )
            if self._use_csd():
                drag = ft.WindowDragArea(
                    expand=True,
                    maximizable=True,
                    content=ft.Container(
                        content=self.title_hit,
                        alignment=ft.Alignment.CENTER,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                    ),
                )
                win_btns = ft.Row(
                    [
                        ft.IconButton(
                            ft.Icons.MINIMIZE,
                            icon_size=18,
                            tooltip="Minimize",
                            icon_color=config.ON_SURFACE_VARIANT,
                            on_click=self._win_minimize,
                        ),
                        ft.IconButton(
                            ft.Icons.CROP_SQUARE,
                            icon_size=18,
                            tooltip="Maximize",
                            icon_color=config.ON_SURFACE_VARIANT,
                            on_click=self._win_toggle_max,
                        ),
                        ft.IconButton(
                            ft.Icons.CLOSE,
                            icon_size=18,
                            tooltip="Close",
                            icon_color=config.ON_SURFACE_VARIANT,
                            on_click=lambda e: self.page.run_task(self._win_close_async, e),
                        ),
                    ],
                    spacing=0,
                )
                self._header_shell = ft.Container(
                    height=0,
                    opacity=0,
                    ignore_interactions=True,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                    left=0,
                    right=0,
                    top=0,
                    bgcolor=config.SURFACE_VARIANT,
                    border=ft.border.only(bottom=ft.BorderSide(1, ui_theme.outline_muted(alpha=0.55))),
                    padding=0,
                    on_hover=self._on_header_chrome_hover,
                    content=ft.Row(
                        [menu, drag, self._license_banner_host, win_btns],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            else:
                self._header_shell = ft.Container(
                    height=0,
                    opacity=0,
                    ignore_interactions=True,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                    left=0,
                    right=0,
                    top=0,
                    bgcolor=config.SURFACE_VARIANT,
                    border=ft.border.only(bottom=ft.BorderSide(1, ui_theme.outline_muted(alpha=0.55))),
                    padding=0,
                    on_hover=self._on_header_chrome_hover,
                    content=ft.Row(
                        [menu, ft.Container(expand=True), self._license_banner_host],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )

        main_column_children.append(self._main_row)
        body_column = ft.Column(main_column_children, expand=True, spacing=0)
        # CSD uses page horizontal padding 0 so the menu bar reaches the window edges; inset only the body.
        main_area: ft.Control = (
            ft.Container(
                content=body_column,
                expand=True,
                padding=ft.padding.symmetric(horizontal=12, vertical=0),
            )
            if self._use_csd()
            else body_column
        )

        stack_children: list[ft.Control] = [main_area]
        if not self.page.web and self._header_shell is not None:
            # Thin top edge only: a tall invisible strip overlapped the tab bar and
            # opened the menu bar when moving the pointer toward Compare.
            stack_children.append(
                ft.Container(
                    height=12,
                    left=0,
                    right=0,
                    top=0,
                    bgcolor=ft.Colors.with_opacity(0.001, ft.Colors.WHITE),
                    on_hover=self._on_header_strip_hover,
                )
            )
            stack_children.append(self._header_shell)

        self._rebuild_topic_pills()
        self._sync_version_toolbar_state()
        self.reflow_columns()
        self._margin_gen += 1
        self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)
        self._refresh_compare_diff_immediate()
        self._refresh_tab_toolbar()
        return ft.Stack(stack_children, expand=True, clip_behavior=ft.ClipBehavior.NONE)
