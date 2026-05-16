"""Content tree (document outline): ATX heading parse + left-sidebar UI."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import flet as ft

from iterthink import config
from . import ui_theme
from .constants import KI_TAB_ICON_PX, SIDEBAR_INNER_BORDER_RADIUS_PX, SIDEBAR_TOOLBAR_ROW_H_PX, TAB_PRESENT
from .util import ctrl_on_page as _ctrl_on_page

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_OPEN = re.compile(r"^\s*```")


@dataclass(frozen=True)
class ContentHeading:
    level: int
    title: str
    offset: int


def _strip_closing_hashes(title: str) -> str:
    t = title.rstrip()
    if " #" in t or t.endswith("#"):
        t = re.sub(r"\s+#+\s*$", "", t)
    return t.strip()


def parse_markdown_headings(text: str) -> list[ContentHeading]:
    """Return ATX headings (# … ######) with buffer offsets; skip fenced code blocks."""
    out: list[ContentHeading] = []
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        stripped = body.strip()
        if _FENCE_OPEN.match(stripped):
            in_fence = not in_fence
            offset += len(line)
            continue
        if not in_fence:
            m = _ATX_HEADING.match(body)
            if m:
                level = len(m.group(1))
                title = _strip_closing_hashes(m.group(2))
                if title:
                    out.append(ContentHeading(level=level, title=title, offset=offset))
        offset += len(line)
    return out


def find_next_match_index(buf: str, needle: str, start: int) -> int | None:
    """Index of next ``needle`` at/after ``start``, wrapping to 0 when needed."""
    if not needle:
        return None
    s = max(0, min(start, len(buf)))
    i = buf.find(needle, s)
    if i >= 0:
        return i
    if s > 0:
        i = buf.find(needle, 0)
        if i >= 0:
            return i
    return None


class MarkdownStudioContentTree:
    _left_sidebar_tab: int
    content_tree_column: ft.Column
    _content_sidebar_panel: ft.Column
    _content_find_field: ft.TextField
    _content_replace_field: ft.TextField
    _content_find_bar: ft.Container
    _content_replace_bar: ft.Container
    _content_find_replace_column: ft.Column
    _content_find_pos: int
    _left_sidebar_toolbar_band: ft.Container | None
    _left_sidebar_tree_well: ft.Container | None
    _left_sidebar_content_well: ft.Container | None

    def _init_content_find_replace_ui(self, hint_style: ft.TextStyle) -> None:
        self._content_find_pos = 0

        def _rim_field(container: ft.Container, field: ft.TextField) -> None:
            def _on_focus(_e: ft.ControlEvent) -> None:
                container.border = ft.Border.all(1, config.PRIMARY_COLOR)
                if _ctrl_on_page(container):
                    container.update()

            def _on_blur(_e: ft.ControlEvent) -> None:
                container.border = ft.Border.all(1, ui_theme.outline_muted())
                if _ctrl_on_page(container):
                    container.update()

            field.on_focus = _on_focus
            field.on_blur = _on_blur

        def _sidebar_field(
            hint: str,
            *,
            on_submit: Callable[[ft.ControlEvent], Any] | None = None,
        ) -> tuple[ft.TextField, ft.Container]:
            tf = ft.TextField(
                hint_text=hint,
                dense=True,
                filled=True,
                fill_color=config.SURFACE,
                focused_bgcolor=config.SURFACE,
                bgcolor=config.SURFACE,
                border=ft.InputBorder.NONE,
                text_size=12,
                color=config.ON_SURFACE,
                hint_style=hint_style,
                cursor_color=config.PRIMARY_COLOR,
                content_padding=ft.padding.symmetric(horizontal=8, vertical=0),
                expand=True,
                on_submit=on_submit,
            )
            box = ft.Container(
                expand=True,
                height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
                bgcolor=config.SURFACE,
                border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
                border=ft.Border.all(1, ui_theme.outline_muted()),
                alignment=ft.Alignment.CENTER_LEFT,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                content=tf,
            )
            _rim_field(box, tf)
            return tf, box

        self._content_find_field, self._content_find_bar = _sidebar_field(
            "Find…",
            on_submit=lambda _e: self._content_find_next(),
        )
        self._content_find_field.on_change = lambda _e: self._on_content_find_change()
        self._content_replace_field, self._content_replace_bar = _sidebar_field(
            "Replace…",
            on_submit=lambda _e: self._content_replace_one(),
        )

        btn_style = ft.ButtonStyle(padding=ft.padding.all(2))
        btn_h = float(SIDEBAR_TOOLBAR_ROW_H_PX)
        find_btn = ft.IconButton(
            ft.Icons.SEARCH,
            icon_size=KI_TAB_ICON_PX,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Find next",
            style=btn_style,
            height=btn_h,
            width=btn_h,
            on_click=lambda _e: self._content_find_next(),
        )
        replace_btn = ft.IconButton(
            ft.Icons.FIND_REPLACE,
            icon_size=KI_TAB_ICON_PX,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Replace",
            style=btn_style,
            height=btn_h,
            width=btn_h,
            on_click=lambda _e: self._content_replace_one(),
        )
        replace_all_btn = ft.IconButton(
            ft.Icons.REPEAT,
            icon_size=KI_TAB_ICON_PX,
            icon_color=config.PRIMARY_COLOR,
            tooltip="Replace all",
            style=btn_style,
            height=btn_h,
            width=btn_h,
            on_click=lambda _e: self._content_replace_all(),
        )

        self._content_find_replace_column = ft.Column(
            [
                ft.Row(
                    [self._content_find_bar, find_btn],
                    spacing=4,
                    height=btn_h,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [self._content_replace_bar, replace_btn, replace_all_btn],
                    spacing=4,
                    height=btn_h,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=4,
            tight=True,
        )
        self._content_sidebar_panel = ft.Column(
            [self._content_find_replace_column, self.content_tree_column],
            expand=True,
            spacing=4,
        )

    def _sync_content_find_replace_field_theme(self, hint_style: ft.TextStyle) -> None:
        for tf, box in (
            (self._content_find_field, self._content_find_bar),
            (self._content_replace_field, self._content_replace_bar),
        ):
            tf.color = config.ON_SURFACE
            tf.hint_style = hint_style
            tf.fill_color = config.SURFACE
            tf.bgcolor = config.SURFACE
            tf.focused_bgcolor = config.SURFACE
            box.bgcolor = config.SURFACE
            box.border = ft.Border.all(1, ui_theme.outline_muted())
            if _ctrl_on_page(tf):
                tf.update()
            if _ctrl_on_page(box):
                box.update()

    def _on_content_find_change(self) -> None:
        self._content_find_pos = 0

    def _content_find_needle(self) -> str:
        return self._content_find_field.value or ""

    def _content_replace_text(self) -> str:
        return self._content_replace_field.value or ""

    def _content_apply_buffer(self, new_text: str, sel_start: int, sel_end: int) -> None:
        self.editor.value = new_text
        self.editor.selection = ft.TextSelection(sel_start, sel_end)
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._after_editor_programmatic_change()
        if getattr(self, "_left_sidebar_tab", 0) == 1:
            self._rebuild_content_tree()

    async def _content_ensure_compose_editor(self) -> None:
        if self._main_tab_index != TAB_PRESENT:
            await self._request_tab_switch_async(TAB_PRESENT)
        if getattr(self, "_focus_view_mode", "edit") == "preview":
            self._focus_view_mode = "edit"
            self._apply_focus_preview_mode()

    def _content_find_next(self) -> None:
        needle = self._content_find_needle()
        if not needle:
            self._snack("Enter text to find.")
            return
        buf = self.editor.value or ""
        idx = find_next_match_index(buf, needle, self._content_find_pos)
        if idx is None:
            self._snack("No matches.")
            self._content_find_pos = 0
            return
        self._content_find_pos = idx + len(needle)
        self.page.run_task(self._content_select_range, idx, idx + len(needle))

    async def _content_select_range(self, start: int, end: int) -> None:
        await self._content_ensure_compose_editor()
        n = len(self.editor.value or "")
        a = max(0, min(int(start), n))
        b = max(a, min(int(end), n))
        try:
            await self.editor.focus()
        except BaseException:
            pass
        self.editor.selection = ft.TextSelection(a, b)
        if _ctrl_on_page(self.editor):
            self.editor.update()

    def _content_replace_one(self) -> None:
        needle = self._content_find_needle()
        if not needle:
            self._snack("Enter text to find.")
            return
        repl = self._content_replace_text()
        buf = self.editor.value or ""
        sel = self.editor.selection
        if sel is not None and not sel.is_collapsed and sel.get_selected_text(buf) == needle:
            a, b = int(sel.start), int(sel.end)
            new_buf = buf[:a] + repl + buf[b:]
            self._content_find_pos = a + len(repl)
            self.page.run_task(self._content_apply_replace_async, new_buf, a, a + len(repl))
            return
        idx = find_next_match_index(buf, needle, self._content_find_pos)
        if idx is None:
            self._snack("No matches.")
            self._content_find_pos = 0
            return
        a, b = idx, idx + len(needle)
        new_buf = buf[:a] + repl + buf[b:]
        self._content_find_pos = a + len(repl)
        self.page.run_task(self._content_apply_replace_async, new_buf, a, a + len(repl))

    async def _content_apply_replace_async(
        self, new_text: str, sel_start: int, sel_end: int
    ) -> None:
        await self._content_ensure_compose_editor()
        self._content_apply_buffer(new_text, sel_start, sel_end)

    def _content_replace_all(self) -> None:
        needle = self._content_find_needle()
        if not needle:
            self._snack("Enter text to find.")
            return
        repl = self._content_replace_text()
        buf = self.editor.value or ""
        if needle not in buf:
            self._snack("No matches.")
            return
        count = buf.count(needle)
        new_buf = buf.replace(needle, repl)
        self._content_find_pos = 0
        self.page.run_task(self._content_apply_replace_async, new_buf, 0, 0)
        self._snack(f"Replaced {count} occurrence{'s' if count != 1 else ''}.")

    def _is_content_tree_eligible(self) -> bool:
        cur = getattr(self, "current_path", None)
        return cur is not None and Path(cur).suffix.lower() == ".md"

    def _build_left_sidebar_tab_button(
        self, *, icon: str, tooltip: str, idx: int
    ) -> ft.Container:
        is_active = self._left_sidebar_tab == idx
        return ft.Container(
            content=ft.Icon(
                icon,
                size=KI_TAB_ICON_PX,
                color=config.ON_SURFACE if is_active else config.ON_SURFACE_VARIANT,
            ),
            expand=1,
            height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            alignment=ft.Alignment.CENTER,
            tooltip=tooltip,
            on_click=lambda _e, i=idx: self._select_left_sidebar_tab(i),
            border=ft.border.only(
                bottom=ft.BorderSide(
                    2,
                    config.HIGHLIGHT if is_active else ft.Colors.TRANSPARENT,
                )
            ),
        )

    def _select_left_sidebar_tab(self, idx: int) -> None:
        if idx == self._left_sidebar_tab:
            return
        self._left_sidebar_tab = idx
        self.left_panel.content = self._build_left_column()
        if idx == 1:
            self._rebuild_content_tree()
        if _ctrl_on_page(self.left_panel):
            self.left_panel.update()

    def _content_tree_empty_message(self, text: str) -> ft.Control:
        return ft.Container(
            content=ft.Text(text, size=12, color=config.ON_SURFACE_VARIANT),
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
        )

    def _content_tree_heading_row(self, heading: ContentHeading) -> ft.Control:
        pad = ft.padding.only(left=max(0, heading.level - 1) * 12, top=0, bottom=0)
        label = ft.Text(
            heading.title,
            size=12,
            color=config.ON_SURFACE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        return ft.Container(
            content=ft.GestureDetector(
                mouse_cursor=ft.MouseCursor.CLICK,
                on_tap=lambda _e, off=heading.offset: self.page.run_task(
                    self._on_content_heading_tap, off
                ),
                content=ft.Container(content=label, padding=ft.Padding.symmetric(horizontal=8, vertical=2)),
            ),
            padding=pad,
            tooltip=heading.title,
        )

    def _rebuild_content_tree(self) -> None:
        col = getattr(self, "content_tree_column", None)
        if col is None:
            return
        col.controls.clear()
        if not self._is_content_tree_eligible():
            col.controls.append(
                self._content_tree_empty_message("Open a markdown file to see headings.")
            )
        else:
            headings = parse_markdown_headings(self.editor.value or "")
            if not headings:
                col.controls.append(
                    self._content_tree_empty_message("No headings in this document.")
                )
            else:
                col.controls.extend(self._content_tree_heading_row(h) for h in headings)
        if _ctrl_on_page(col) and getattr(self, "_left_sidebar_tab", 0) == 1:
            col.update()

    async def _on_content_heading_tap(self, offset: int) -> None:
        if self._main_tab_index != TAB_PRESENT:
            await self._request_tab_switch_async(TAB_PRESENT)
        if getattr(self, "_focus_view_mode", "edit") == "preview":
            self._focus_view_mode = "edit"
            self._apply_focus_preview_mode()
        off = max(0, min(int(offset), len(self.editor.value or "")))
        try:
            await self.editor.focus()
        except BaseException:
            pass
        self.editor.selection = ft.TextSelection(off, off)
        if _ctrl_on_page(self.editor):
            self.editor.update()
