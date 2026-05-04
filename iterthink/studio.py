"""Flet UI: MarkdownStudio with editor–margin vertical sync."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any

import flet as ft
from flet.controls.types import PagePlatform
from ollama import AsyncClient

from iterthink import config, prompts, settings_ui, store_db
from iterthink.prompts import TOPIC_CHANGE, TOPIC_DISCUSS, TOPIC_EVALUATE
from iterthink.diff_card import SemanticKind, build_unified_spans
from iterthink.margin import (
    distribute_heights,
    estimate_total_editor_height,
    paragraph_index_at_offset,
    paragraph_slot_weights,
    split_paragraphs,
)
from iterthink.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.paragraph_align import old_text_per_new_slot
from iterthink.paragraph_semantics import classify_paragraph_slots_batch
from iterthink.tree import build_md_tree, filter_md_tree

# Typing idle before autosave. Margin diff is vs last_saved_text, so a longer delay
# keeps paragraph change highlights visible while you pause.
AUTOSAVE_IDLE_SEC = 6.0

# Collapsed side rails: narrow strip, square (no card rounding), transparent fill.
COLLAPSED_RAIL_WIDTH_PX = 12

_HELP_MD_PATH = Path(__file__).resolve().parent / "help.md"


_QUICK_PILL_LABEL_DE: dict[str, str] = {
    "devil_advocate": "Devil's advocate",
    "clarify_intent": "Clarify",
    "grammar": "Verbessern",
    "summarize": "Kürzen",
    "tone": "Ton",
    "latex": "LaTeX",
    "brainstorm": "Brainstorm",
}


def _quick_pill_icon(action_id: str):
    return {
        "devil_advocate": ft.Icons.GAVEL,
        "clarify_intent": ft.Icons.HELP_OUTLINE,
        "grammar": ft.Icons.EDIT_DOCUMENT,
        "summarize": ft.Icons.CONTENT_CUT,
        "tone": ft.Icons.TUNE,
        "latex": ft.Icons.FUNCTIONS,
        "brainstorm": ft.Icons.LIGHTBULB_OUTLINE,
    }.get(action_id, ft.Icons.AUTO_AWESOME)


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    """Flet raises RuntimeError when reading .page before the control is mounted."""
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


class MarkdownStudio:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._store_dir_resolved = config.STORE_DIR.resolve()
        self._fp_documents = ft.FilePicker()
        self._fp_store = ft.FilePicker()
        self._menu_bar: ft.MenuBar | None = None
        self.ollama = AsyncClient(host=config.OLLAMA_HOST) if config.OLLAMA_HOST else AsyncClient()
        self._db = store_db.connect()
        store_db.init_schema(self._db)
        self.ollama_model: str = store_db.settings_get(self._db, store_db.SETTINGS_CHAT) or config.DEFAULT_OLLAMA_MODEL
        self.ollama_embed_model: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_EMBED) or config.DEFAULT_OLLAMA_EMBED_MODEL
        )
        self.current_path: Path | None = None
        self.last_saved_text: str = ""
        self.last_selection: str = ""
        self.left_open: bool = True

        self._last_editor_h: float = 480.0
        self._last_editor_content_w: float = 520.0
        self._margin_gen: int = 0
        self._meaning_gen: int = 0

        self._slot_roots: list[ft.Container] = []
        self._slot_bodies: list[ft.Text] = []
        self._slot_stripes: list[ft.Container] = []
        self._semantic: list[SemanticKind | None] = []
        self._slot_override: dict[int, str] = {}
        self._slot_override_snap: dict[int, str] = {}

        self._header_hide_gen: int = 0
        self._header_shell: ft.Container | None = None

        self._gutter = ft.Container(width=2, bgcolor=ft.Colors.with_opacity(0.35, ft.Colors.GREY_700))

        self.editor = ft.TextField(
            multiline=True,
            max_lines=None,
            min_lines=1,
            border=ft.InputBorder.NONE,
            filled=False,
            hint_text="Write…",
            text_style=ft.TextStyle(font_family="monospace", size=14, height=1.6, color=ft.Colors.GREY_100),
            cursor_color=config.FEDORA_BLUE,
            selection_color=config.SELECTION_OVERLAY,
            enable_interactive_selection=True,
            on_change=self._on_editor_change,
            on_selection_change=self._on_selection_change,
            on_size_change=self._on_editor_size_change,
        )

        self._editor_shell = ft.Container(content=self.editor, expand=True)
        self._margin_column = ft.Column(spacing=0, tight=True)
        self._margin_shell = ft.Container(content=self._margin_column, expand=True)

        self.sync_row = ft.Row(
            [
                self._editor_shell,
                self._gutter,
                self._margin_shell,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        self.sheet_scroll = ft.Column(
            controls=[self.sync_row],
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            scroll_interval=48,
            on_scroll=self._on_sheet_scroll,
        )

        self.filename_text = ft.Text(
            "iterthink - No file",
            size=16,
            weight=ft.FontWeight.W_500,
            color=ft.Colors.GREY_200,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
        )
        self.dirty_dot = ft.Text(
            "•",
            size=18,
            weight=ft.FontWeight.W_700,
            color=config.FEDORA_BLUE,
            visible=False,
        )
        self.title_hit = ft.Container(
            content=ft.Row([self.filename_text, self.dirty_dot], tight=True, spacing=6),
            tooltip="",
        )
        self._autosave_gen: int = 0

        self.tree_column = ft.Column(spacing=0, tight=True, scroll=ft.ScrollMode.AUTO, expand=True)

        self.tree_search_field = ft.TextField(
            hint_text="Search files…",
            dense=True,
            filled=True,
            bgcolor=config.SURFACE,
            border_radius=8,
            text_size=12,
            cursor_color=config.FEDORA_BLUE,
            border_color=ft.Colors.GREY_700,
            focused_border_color=config.FEDORA_BLUE,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=6),
            expand=True,
            on_change=self._on_tree_search_change,
        )
        self._tree_add_menu = ft.PopupMenuButton(
            icon=ft.Icons.ADD,
            icon_size=22,
            icon_color=config.FEDORA_BLUE,
            tooltip="New…",
            style=ft.ButtonStyle(padding=ft.padding.all(4)),
            menu_position=ft.PopupMenuPosition.UNDER,
            items=[
                ft.PopupMenuItem(
                    content=ft.Text("Markdown file", size=13),
                    on_click=lambda e: self.page.run_task(self.new_file, e),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Folder", size=13),
                    on_click=lambda e: self.page.run_task(self.new_folder, e),
                ),
            ],
        )

        self.left_panel = ft.Container(
            width=260,
            margin=12,
            padding=8,
            bgcolor=config.SIDEBAR_SURFACE,
            border_radius=15,
            animate=ft.Animation(300, ft.AnimationCurve.DECELERATE),
        )
        self.center_panel = ft.Container(expand=True, padding=ft.Padding.symmetric(horizontal=10, vertical=8), bgcolor=config.SURFACE)

        self._main_row: ft.Row | None = None

        self.right_open: bool = True
        self._ki_topic_index: int = 0
        self._chat_api_messages: list[dict[str, str]] = []

        self._pill_row_discuss = ft.Row(spacing=8, wrap=True, run_spacing=6)
        self._pill_row_change = ft.Row(spacing=8, wrap=True, run_spacing=6)
        self._pill_row_evaluate = ft.Row(spacing=8, wrap=True, run_spacing=6)

        self._evaluate_placeholder = ft.Text(
            "Evaluate — coming later.",
            size=13,
            color=ft.Colors.GREY_500,
            italic=True,
        )
        self._evaluate_body = ft.Column(
            [self._evaluate_placeholder, self._pill_row_evaluate],
            tight=True,
            spacing=8,
        )

        self._pill_area_discuss = ft.Container(visible=True, content=self._pill_row_discuss)
        self._pill_area_change = ft.Container(visible=False, content=self._pill_row_change)
        self._pill_area_evaluate = ft.Container(visible=False, content=self._evaluate_body)

        self._btn_topic_discuss = ft.TextButton(
            "Discuss",
            on_click=lambda e: self._set_ki_topic(0),
        )
        self._btn_topic_change = ft.TextButton(
            "Change",
            on_click=lambda e: self._set_ki_topic(1),
        )
        self._btn_topic_evaluate = ft.TextButton(
            "Evaluate",
            on_click=lambda e: self._set_ki_topic(2),
        )
        self._topic_strip = ft.Row(
            [self._btn_topic_discuss, self._btn_topic_change, self._btn_topic_evaluate],
            spacing=2,
        )

        self._chat_history = ft.ListView(
            expand=True,
            spacing=8,
            padding=ft.padding.all(8),
            auto_scroll=True,
        )
        self._chat_input = ft.TextField(
            hint_text="Frag die KI etwas zum Dokument…",
            min_lines=1,
            max_lines=4,
            multiline=True,
            expand=True,
            text_size=13,
            border_color=ft.Colors.GREY_700,
            cursor_color=config.FEDORA_BLUE,
            on_submit=lambda e: self.page.run_task(self._send_chat_message, e),
        )
        self._chat_send_btn = ft.IconButton(
            icon=ft.Icons.SEND,
            tooltip="Senden",
            icon_color=config.FEDORA_BLUE,
            on_click=lambda e: self.page.run_task(self._send_chat_message, e),
        )
        self._chat_model_btn = ft.IconButton(
            icon=ft.Icons.MODEL_TRAINING,
            icon_size=20,
            icon_color=ft.Colors.GREY_400,
            tooltip=self._chat_model_tooltip(),
            style=ft.ButtonStyle(padding=ft.padding.all(4)),
            on_click=lambda e: self.page.run_task(settings_ui.open_settings_dialog, self),
        )
        self._right_chat_section = ft.Container(
            expand=True,
            bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.BLACK),
            border_radius=10,
            padding=ft.padding.all(6),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    self._chat_history,
                    ft.Row(
                        [self._chat_input, self._chat_model_btn, self._chat_send_btn],
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        spacing=4,
                    ),
                ],
                expand=True,
                spacing=8,
            ),
        )

        self._right_ki_column = ft.Column(
            [
                ft.Text("KI", size=12, weight=ft.FontWeight.W_600, color=ft.Colors.GREY_500),
                self._topic_strip,
                ft.Container(
                    content=ft.Column(
                        [self._pill_area_discuss, self._pill_area_change, self._pill_area_evaluate],
                        tight=True,
                        spacing=0,
                    ),
                    padding=ft.padding.only(bottom=4),
                ),
                self._right_chat_section,
            ],
            expand=True,
            spacing=8,
        )

        self.right_panel = ft.Container(
            width=260,
            margin=12,
            padding=8,
            bgcolor=config.SIDEBAR_SURFACE,
            border_radius=15,
            content=ft.Container(),
            animate=ft.Animation(300, ft.AnimationCurve.DECELERATE),
        )

        self.page.on_keyboard_event = self._on_page_keyboard

    def _sync_ki_topic_buttons(self) -> None:
        ix = self._ki_topic_index
        for i, btn in enumerate((self._btn_topic_discuss, self._btn_topic_change, self._btn_topic_evaluate)):
            sel = i == ix
            btn.style = ft.ButtonStyle(
                color=config.FEDORA_BLUE if sel else ft.Colors.GREY_400,
                bgcolor=ft.Colors.with_opacity(0.18, config.FEDORA_BLUE) if sel else None,
            )
            if _ctrl_on_page(btn):
                btn.update()

    def _set_ki_topic(self, index: int) -> None:
        self._ki_topic_index = max(0, min(2, int(index)))
        self._pill_area_discuss.visible = self._ki_topic_index == 0
        self._pill_area_change.visible = self._ki_topic_index == 1
        self._pill_area_evaluate.visible = self._ki_topic_index == 2
        self._sync_ki_topic_buttons()
        for c in (self._pill_area_discuss, self._pill_area_change, self._pill_area_evaluate):
            if _ctrl_on_page(c):
                c.update()

    def toggle_right(self, _e: ft.ControlEvent | None = None) -> None:
        self.right_open = not self.right_open
        self.right_panel.content = self._build_right_column()
        self.reflow_columns()
        if _ctrl_on_page(self.right_panel):
            self.right_panel.update()

    def _ki_rail_collapse_strip(self) -> ft.Control:
        """Left edge of KI card: thin hairline + hover pill; tap collapses."""
        return self._pane_split_handle(
            tooltip="Collapse KI panel",
            on_toggle=self.toggle_right,
            hairline_after=True,
        )

    def _build_right_column(self) -> ft.Control:
        if not self.right_open:
            return ft.Row(
                [
                    ft.Container(expand=True),
                    self._pane_split_handle(
                        tooltip="Show KI panel",
                        on_toggle=self.toggle_right,
                        hairline_after=False,
                    ),
                    ft.Container(expand=True),
                ],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        return ft.Row(
            [
                self._ki_rail_collapse_strip(),
                ft.Container(content=self._right_ki_column, expand=True, padding=ft.padding.only(left=4)),
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _rebuild_topic_pills(self) -> None:
        def fill_row(row: ft.Row, topic: str) -> None:
            row.controls.clear()
            for a in prompts.actions_for_topic(topic):
                label = _QUICK_PILL_LABEL_DE.get(a.id, a.label)
                aid = a.id
                row.controls.append(
                    ft.FilledButton(
                        content=label,
                        icon=_quick_pill_icon(aid),
                        style=ft.ButtonStyle(
                            padding=ft.padding.symmetric(horizontal=10, vertical=8),
                        ),
                        on_click=lambda e, action_id=aid: self.page.run_task(self._quick_margin_action, action_id),
                    )
                )

        fill_row(self._pill_row_discuss, TOPIC_DISCUSS)
        fill_row(self._pill_row_change, TOPIC_CHANGE)
        fill_row(self._pill_row_evaluate, TOPIC_EVALUATE)

        ev = prompts.actions_for_topic(TOPIC_EVALUATE)
        self._evaluate_placeholder.visible = len(ev) == 0
        self._pill_row_evaluate.visible = len(ev) > 0
        if _ctrl_on_page(self._evaluate_placeholder):
            self._evaluate_placeholder.update()
        if _ctrl_on_page(self._pill_row_evaluate):
            self._pill_row_evaluate.update()

        for row in (self._pill_row_discuss, self._pill_row_change, self._pill_row_evaluate):
            if _ctrl_on_page(row):
                row.update()

    def _invalidate_header_hide(self) -> None:
        self._header_hide_gen += 1

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
        if _ctrl_on_page(sh):
            sh.update()

    def _expand_header_bar(self) -> None:
        self._invalidate_header_hide()
        sh = self._header_shell
        if not sh:
            return
        sh.height = 50
        sh.opacity = 1.0
        if _ctrl_on_page(sh):
            sh.update()

    def _on_header_strip_hover(self, e: ft.ControlEvent) -> None:
        if self.page.web or self._header_shell is None:
            return
        if e.data:
            self._expand_header_bar()
        else:
            self._schedule_header_hide()

    def _on_header_chrome_hover(self, e: ft.ControlEvent) -> None:
        if self.page.web or self._header_shell is None:
            return
        if e.data:
            self._invalidate_header_hide()
        else:
            self._schedule_header_hide()

    def _window_width(self) -> int:
        try:
            w = self.page.window.width  # type: ignore[attr-defined]
            if w and w > 0:
                return int(w)
        except Exception:
            pass
        return max(900, int(getattr(self.page, "width", None) or 1200))

    def _on_page_keyboard(self, e: ft.KeyboardEvent) -> None:
        key = (e.key or "").lower()
        if key != "j":
            return
        if not (e.ctrl or e.meta):
            return
        self.toggle_right()

    async def _quick_margin_action(self, action_id: str) -> None:
        buf = self.editor.value or ""
        sel = self.editor.selection
        selected = ""
        if sel is not None and not sel.is_collapsed:
            selected = sel.get_selected_text(buf).strip()
        off = sel.start if sel is not None else 0
        idx = paragraph_index_at_offset(buf, off)
        await self.run_margin_action(action_id, idx, text_override=selected if selected else None)

    def _append_chat_line(self, role: str, text: str) -> None:
        bg = ft.Colors.with_opacity(0.35, ft.Colors.GREY_900) if role == "user" else ft.Colors.with_opacity(0.25, config.FEDORA_BLUE)
        align = ft.Alignment.CENTER_RIGHT if role == "user" else ft.Alignment.CENTER_LEFT
        bubble = ft.Container(
            content=ft.Text(text, size=13, selectable=True, color=ft.Colors.GREY_100),
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=bg,
            border_radius=10,
            alignment=align,
        )
        self._chat_history.controls.append(bubble)
        if _ctrl_on_page(self._chat_history):
            self._chat_history.update()

    async def _send_chat_message(self, _e: ft.ControlEvent | None = None) -> None:
        raw = (self._chat_input.value or "").strip()
        if not raw:
            return
        self._chat_input.value = ""
        if _ctrl_on_page(self._chat_input):
            self._chat_input.update()

        self._append_chat_line("user", raw)

        doc = (self.editor.value or "")[:8000]
        messages: list[dict[str, str]] = [{"role": "system", "content": config.CHAT_SYSTEM}]
        if doc.strip() and not self._chat_api_messages:
            messages.append(
                {
                    "role": "user",
                    "content": "Aktuelles Markdown-Dokument (Auszug):\n```markdown\n" + doc + "\n```",
                }
            )
        messages.extend(self._chat_api_messages)
        messages.append({"role": "user", "content": raw})

        acc = ""
        reply = ft.Text("", size=13, selectable=True, color=ft.Colors.GREY_100)
        wrap = ft.Container(
            content=reply,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.GREY_800),
            border_radius=10,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        self._chat_history.controls.append(wrap)
        if _ctrl_on_page(self._chat_history):
            self._chat_history.update()

        try:
            stream = await self.ollama.chat(
                model=self.ollama_model,
                messages=messages,
                stream=True,
            )
            async for part in stream:
                acc += chat_stream_delta(part)
                reply.value = acc.strip() or "…"
                if _ctrl_on_page(reply):
                    reply.update()
        except BaseException:
            try:
                resp = await self.ollama.chat(
                    model=self.ollama_model,
                    messages=messages,
                    stream=False,
                )
                acc = chat_response_text(resp) or ""
            except BaseException as ex_final:
                reply.value = f"(Fehler) {ollama_error_message(ex_final)}"
                if _ctrl_on_page(reply):
                    reply.update()
                return

        acc = (acc or "").strip()
        reply.value = acc or "(Leere Antwort)"
        if _ctrl_on_page(reply):
            reply.update()
        self._chat_api_messages.append({"role": "user", "content": raw})
        self._chat_api_messages.append({"role": "assistant", "content": acc})

    def _sync_side_panel_chrome(self) -> None:
        """Expanded = rounded sidebar card; collapsed = square transparent rail (hairline from handle)."""
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

    def reflow_columns(self, _e: ft.ControlEvent | None = None) -> None:
        w = self._window_width()
        left_w = max(160, int(w * 0.20)) if self.left_open else COLLAPSED_RAIL_WIDTH_PX
        right_w = max(160, int(w * 0.20)) if self.right_open else COLLAPSED_RAIL_WIDTH_PX
        self.left_panel.width = left_w
        self.right_panel.width = right_w
        self._sync_side_panel_chrome()
        # Editor | gutter | margin: approximate half-width; left/right rail margins (12+12), two row gaps, center pad.
        center_inner = float(w) - float(left_w) - float(right_w) - 24.0 - 24.0 - 20.0 - 20.0 - 20.0
        half = max(120.0, (center_inner - 2.0) * 0.5)
        self._last_editor_content_w = half
        self.page.run_task(self._debounced_margin_rebuild, self._margin_gen)
        self.page.update()

    def _is_dirty(self) -> bool:
        return (self.editor.value or "") != self.last_saved_text

    def _refresh_title_bar(self) -> None:
        if not self.current_path:
            self.filename_text.value = "iterthink - No file"
            self.title_hit.tooltip = ""
        else:
            self.filename_text.value = f"iterthink - {self.current_path.name}"
            self.title_hit.tooltip = str(self.current_path)
        self.dirty_dot.visible = bool(self.current_path) and self._is_dirty()
        if _ctrl_on_page(self.filename_text):
            self.filename_text.update()
            self.dirty_dot.update()
            self.title_hit.update()

    def _on_sheet_scroll(self, _e: ft.OnScrollEvent) -> None:
        """Editor and margin share this scroll surface; hook reserved for layout tweaks."""
        return

    def _on_editor_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        cw = max(120.0, float(e.width))
        self._last_editor_content_w = cw
        reported = float(e.height)
        paras = split_paragraphs(self.editor.value or "")
        est = estimate_total_editor_height(paras, cw)
        # Prefer content-based height when the field likely reports viewport-only size.
        self._last_editor_h = max(reported, est * 0.98)
        self._apply_slot_heights()

    def _on_editor_change(self, _e: ft.ControlEvent) -> None:
        self._refresh_title_bar()
        self._margin_gen += 1
        gen = self._margin_gen
        self.page.run_task(self._debounced_margin_rebuild, gen)
        self._meaning_gen += 1
        mgen = self._meaning_gen
        self.page.run_task(self._meaning_after_idle, mgen)
        if not self.current_path:
            return
        self._autosave_gen += 1
        agen = self._autosave_gen
        self.page.run_task(self._autosave_after_idle, agen)

    async def _debounced_margin_rebuild(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._margin_gen:
            return
        self._rebuild_margin_slots()
        self._apply_slot_heights()
        self._refresh_gutter_color()

    async def _meaning_after_idle(self, gen: int) -> None:
        await asyncio.sleep(2.0)
        if gen != self._meaning_gen:
            return
        if not self.current_path:
            return
        buf = self.editor.value or ""
        saved = self.last_saved_text
        cur = split_paragraphs(buf)
        aligned_old = old_text_per_new_slot(saved, buf)
        items: list[tuple[int, str, str]] = []
        for i, p in enumerate(cur):
            o = aligned_old[i] if i < len(aligned_old) else ""
            if o == p:
                continue
            if i in self._slot_override:
                continue
            while len(self._semantic) <= i:
                self._semantic.append(None)
            items.append((i, o, p))
        if not items:
            return
        doc_path = str(self.current_path.resolve())
        pairs = await classify_paragraph_slots_batch(
            self._db,
            self.ollama,
            chat_model=self.ollama_model,
            embed_model=self.ollama_embed_model,
            doc_path=doc_path,
            items=items,
        )
        for idx, kind in pairs:
            if kind not in ("STABLE", "NEW"):
                continue
            if idx < len(self._semantic):
                self._semantic[idx] = kind
        self._refresh_stripes_and_gutter()

    async def _autosave_after_idle(self, gen: int) -> None:
        await asyncio.sleep(AUTOSAVE_IDLE_SEC)
        if gen != self._autosave_gen:
            return
        await self.save_file(silent=True)

    def _on_selection_change(self, e: ft.TextSelectionChangeEvent) -> None:
        t = (e.selected_text or "").strip()
        if t:
            self.last_selection = e.selected_text or ""

    def _chat_model_tooltip(self) -> str:
        return f"Chat model: {self.ollama_model} — open Settings to change"

    def _refresh_chat_model_button(self) -> None:
        self._chat_model_btn.tooltip = self._chat_model_tooltip()
        if _ctrl_on_page(self._chat_model_btn):
            self._chat_model_btn.update()

    def _snack(self, msg: str) -> None:
        self.page.snack_bar = ft.SnackBar(ft.Text(msg))
        self.page.snack_bar.open = True
        self.page.update()

    def ensure_file_pickers(self) -> None:
        # Flet 0.80+: FilePicker is a Service; overlay causes "Unknown control: FilePicker".
        if self._fp_documents not in self.page.services:
            self.page.services.append(self._fp_documents)
            self.page.services.append(self._fp_store)
            self.page.update()

    def refresh_ollama_client(self) -> None:
        self.ollama = AsyncClient(host=config.OLLAMA_HOST) if config.OLLAMA_HOST else AsyncClient()

    def apply_config_theme(self) -> None:
        self.editor.cursor_color = config.FEDORA_BLUE
        self.editor.selection_color = config.SELECTION_OVERLAY
        self.dirty_dot.color = config.FEDORA_BLUE
        self._sync_side_panel_chrome()
        self.center_panel.bgcolor = config.SURFACE
        if self._header_shell:
            self._header_shell.bgcolor = config.SURFACE_VARIANT
        if self._menu_bar:
            self._menu_bar.style = ft.MenuStyle(bgcolor=config.SURFACE_VARIANT)
        self.page.theme = ft.Theme(
            color_scheme=ft.ColorScheme(
                primary=config.FEDORA_BLUE,
                on_primary=ft.Colors.WHITE,
                surface=config.SURFACE_VARIANT,
                on_surface=ft.Colors.GREY_100,
                surface_container=config.SURFACE,
            ),
        )
        self.left_panel.content = self._build_left_column()
        self.right_panel.content = self._build_right_column()
        if _ctrl_on_page(self.editor):
            self.editor.update()
            self.dirty_dot.update()
        if _ctrl_on_page(self.left_panel):
            self.left_panel.update()
        if _ctrl_on_page(self.right_panel):
            self.right_panel.update()
        if _ctrl_on_page(self.center_panel):
            self.center_panel.update()
        if self._header_shell and _ctrl_on_page(self._header_shell):
            self._header_shell.update()
        if self._menu_bar and _ctrl_on_page(self._menu_bar):
            self._menu_bar.update()
        self.page.update()

    def _paragraph_for_index(self, idx: int) -> str:
        paras = split_paragraphs(self.editor.value or "")
        if 0 <= idx < len(paras):
            return paras[idx]
        return ""

    def _stripe_color(self, idx: int, changed: bool) -> str | None:
        if not changed:
            return None
        if idx < len(self._semantic) and self._semantic[idx] == "NEW":
            return ft.Colors.AMBER_400
        if idx < len(self._semantic) and self._semantic[idx] == "STABLE":
            return config.FEDORA_BLUE
        return ft.Colors.with_opacity(0.5, ft.Colors.GREY_500)

    def _refresh_gutter_color(self) -> None:
        buf = self.editor.value or ""
        saved = self.last_saved_text
        cur = split_paragraphs(buf)
        aligned_old = old_text_per_new_slot(saved, buf)
        any_new = False
        any_stable = False
        any_pending = False
        for i, p in enumerate(cur):
            o = aligned_old[i] if i < len(aligned_old) else ""
            if o == p:
                continue
            sem = self._semantic[i] if i < len(self._semantic) else None
            if sem == "NEW":
                any_new = True
            elif sem == "STABLE":
                any_stable = True
            else:
                any_pending = True
        if any_new:
            self._gutter.bgcolor = ft.Colors.AMBER_400
        elif any_stable and not any_pending:
            self._gutter.bgcolor = config.FEDORA_BLUE
        elif any_stable or any_pending:
            self._gutter.bgcolor = ft.Colors.with_opacity(0.55, ft.Colors.GREY_500)
        else:
            self._gutter.bgcolor = ft.Colors.with_opacity(0.35, ft.Colors.GREY_700)
        if _ctrl_on_page(self._gutter):
            self._gutter.update()

    def _refresh_stripes_and_gutter(self) -> None:
        buf = self.editor.value or ""
        saved = self.last_saved_text
        cur = split_paragraphs(buf)
        aligned_old = old_text_per_new_slot(saved, buf)
        for i, stripe in enumerate(self._slot_stripes):
            o = aligned_old[i] if i < len(aligned_old) else ""
            p = cur[i] if i < len(cur) else ""
            changed = o != p
            c = self._stripe_color(i, changed)
            stripe.bgcolor = c or ft.Colors.TRANSPARENT
            if _ctrl_on_page(stripe):
                stripe.update()
        self._refresh_gutter_color()

    def _apply_slot_heights(self) -> None:
        if not self._slot_roots:
            return
        paras = split_paragraphs(self.editor.value or "")
        wts = paragraph_slot_weights(paras, self._last_editor_content_w)
        heights = distribute_heights(wts, self._last_editor_h)
        n = min(len(self._slot_roots), len(heights))
        for i in range(n):
            self._slot_roots[i].height = heights[i]
        if _ctrl_on_page(self._margin_column):
            self._margin_column.update()

    def _rebuild_margin_slots(self) -> None:
        buf = self.editor.value or ""
        saved = self.last_saved_text
        cur = split_paragraphs(buf)
        aligned_old = old_text_per_new_slot(saved, buf)

        while len(self._semantic) < len(cur):
            self._semantic.append(None)
        while len(self._semantic) > len(cur):
            self._semantic.pop()

        for i in list(self._slot_override.keys()):
            snap = self._slot_override_snap.get(i)
            cur_i = cur[i] if i < len(cur) else ""
            if snap is not None and cur_i != snap:
                self._slot_override.pop(i, None)
                self._slot_override_snap.pop(i, None)

        self._slot_roots.clear()
        self._slot_bodies.clear()
        self._slot_stripes.clear()
        self._margin_column.controls.clear()

        for i, p in enumerate(cur):
            o = aligned_old[i] if i < len(aligned_old) else ""
            changed = o != p

            if i in self._slot_override:
                body = ft.Text(
                    self._slot_override[i],
                    size=12,
                    color=ft.Colors.GREY_300,
                    selectable=True,
                )
            elif changed:
                body = ft.Text(
                    spans=build_unified_spans(o, p, base_size=12, base_color=ft.Colors.GREY_400),
                    selectable=True,
                )
            else:
                body = ft.Text("", size=12)

            stripe = ft.Container(
                width=2,
                bgcolor=self._stripe_color(i, changed) or ft.Colors.TRANSPARENT,
            )

            menu = ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=14,
                icon_color=ft.Colors.with_opacity(0.38, ft.Colors.WHITE),
                tooltip="Paragraph actions",
                style=ft.ButtonStyle(
                    color=ft.Colors.with_opacity(0.45, ft.Colors.WHITE),
                    padding=ft.Padding.all(4),
                ),
                items=[
                    ft.PopupMenuItem(
                        content=a.label,
                        on_click=lambda e, aid=a.id, ix=i: self.page.run_task(self.run_margin_action, aid, ix),
                    )
                    for a in prompts.MARGIN_ACTIONS
                ],
            )

            inner = ft.Row(
                [
                    stripe,
                    menu,
                    ft.Container(content=body, expand=True, padding=ft.Padding.only(left=4, right=2)),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.START,
                expand=True,
            )
            slot = ft.Container(
                content=inner,
                padding=ft.Padding.only(top=2, bottom=2),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            )
            self._slot_stripes.append(stripe)
            self._slot_bodies.append(body)
            self._slot_roots.append(slot)
            self._margin_column.controls.append(slot)

        self._refresh_gutter_color()
        if _ctrl_on_page(self._margin_column):
            self._margin_column.update()

    async def run_margin_action(
        self, action_id: str, idx: int, *, text_override: str | None = None
    ) -> None:
        act = prompts.get_margin_action(action_id)
        if act is None:
            return
        src = text_override if text_override is not None else self._paragraph_for_index(idx)
        para = src.strip()
        if not para:
            self._snack("This paragraph is empty.")
            return
        messages: list[dict[str, str]] = [
            {"role": "system", "content": act.system_prompt},
            {"role": "user", "content": act.user_template.format(text=para)},
        ]
        acc = ""
        body = self._slot_bodies[idx] if 0 <= idx < len(self._slot_bodies) else None
        try:
            stream = await self.ollama.chat(
                model=self.ollama_model,
                messages=messages,
                stream=True,
            )
            async for part in stream:
                acc += chat_stream_delta(part)
                if body is not None and _ctrl_on_page(body):
                    body.spans = None
                    body.value = acc.strip() or "…"
                    body.update()
        except BaseException:
            try:
                resp = await self.ollama.chat(
                    model=self.ollama_model,
                    messages=messages,
                    stream=False,
                )
                acc = chat_response_text(resp) or ""
            except BaseException as ex_final:
                self._snack(ollama_error_message(ex_final))
                return

        acc = (acc or "").strip()
        if not acc:
            self._snack("Empty reply from model.")
            return
        self._slot_override[idx] = acc
        self._slot_override_snap[idx] = self._paragraph_for_index(idx)
        if body is not None and _ctrl_on_page(body):
            body.spans = None
            body.value = acc
            body.update()
        if 0 <= idx < len(self._slot_stripes) and _ctrl_on_page(self._slot_stripes[idx]):
            self._slot_stripes[idx].bgcolor = ft.Colors.with_opacity(0.4, config.FEDORA_BLUE)
            self._slot_stripes[idx].update()
        self._refresh_gutter_color()

    def _on_tree_search_change(self, _e: ft.ControlEvent | None = None) -> None:
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()

    def _rebuild_tree_ui(self) -> None:
        self.tree_column.controls.clear()
        root = config.DOCUMENTS
        if not root.is_dir():
            self.tree_column.controls.append(
                ft.Text(f"Missing folder: {root}", size=12, color=ft.Colors.ORANGE_200)
            )
            return

        tree = build_md_tree(root)
        q = (self.tree_search_field.value or "").strip()
        if q:
            tree = filter_md_tree(tree, q)
            if not tree:
                self.tree_column.controls.append(
                    ft.Text("No matching files.", size=12, color=ft.Colors.GREY_500)
                )
                return

        def render_level(node: dict[str, Any], depth: int = 0) -> list[ft.Control]:
            ctrls: list[ft.Control] = []
            for dirname in sorted(k for k in node if k != "_files"):
                sub = node[dirname]
                inner = render_level(sub, depth + 1)
                ctrls.append(
                    ft.ExpansionTile(
                        title=ft.Text(dirname, size=13, color=ft.Colors.GREY_200),
                        controls=[
                            ft.Container(
                                content=ft.Column(inner, tight=True, spacing=0),
                                padding=ft.Padding.only(left=8),
                            )
                        ],
                        expanded=depth == 0,
                        dense=True,
                        show_trailing_icon=True,
                        leading=None,
                        icon_color=ft.Colors.GREY_500,
                        collapsed_icon_color=ft.Colors.GREY_500,
                    )
                )
            for fname, fpath in sorted(node.get("_files", []), key=lambda x: x[0].lower()):
                ctrls.append(
                    ft.ListTile(
                        dense=True,
                        leading=None,
                        title=ft.Text(fname, size=12, font_family="monospace"),
                        content_padding=ft.Padding.symmetric(horizontal=12, vertical=2),
                        on_click=lambda e, fp=fpath: self.page.run_task(self.open_file, fp),
                    )
                )
            return ctrls

        self.tree_column.controls.extend(render_level(tree))

    async def open_file(self, path: Path) -> None:
        if self.current_path and path != self.current_path and self._is_dirty():
            await self.save_file(silent=True)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not open: {ex}")
            return
        self.current_path = path
        self.last_saved_text = text
        self.editor.value = text
        self._slot_override.clear()
        self._slot_override_snap.clear()
        self._semantic.clear()
        self.editor.update()
        self._margin_gen += 1
        self._rebuild_margin_slots()
        self._apply_slot_heights()
        self._refresh_title_bar()

    def _next_untitled_path(self) -> Path:
        root = config.DOCUMENTS
        root.mkdir(parents=True, exist_ok=True)
        cand = root / "Untitled.md"
        if not cand.exists():
            return cand
        n = 1
        while True:
            cand = root / f"Untitled {n}.md"
            if not cand.exists():
                return cand
            n += 1

    async def new_file(self, _e: ft.ControlEvent | None = None) -> None:
        config.DOCUMENTS.mkdir(parents=True, exist_ok=True)
        path = self._next_untitled_path()
        try:
            path.write_text("", encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not create file: {ex}")
            return
        self._rebuild_tree_ui()
        self.tree_column.update()
        await self.open_file(path)

    def _next_untitled_dir_path(self) -> Path:
        root = config.DOCUMENTS
        root.mkdir(parents=True, exist_ok=True)
        cand = root / "New folder"
        if not cand.exists():
            return cand
        n = 1
        while True:
            cand = root / f"New folder {n}"
            if not cand.exists():
                return cand
            n += 1

    async def new_folder(self, _e: ft.ControlEvent | None = None) -> None:
        config.DOCUMENTS.mkdir(parents=True, exist_ok=True)
        path = self._next_untitled_dir_path()
        try:
            path.mkdir(parents=False)
        except OSError as ex:
            self._snack(f"Could not create folder: {ex}")
            return
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()
        self._snack(f'Created folder "{path.name}".')

    async def save_file(self, _e: ft.ControlEvent | None = None, *, silent: bool = False) -> None:
        if not self.current_path:
            if not silent:
                self._snack("Open or create a note first.")
            return
        buf = self.editor.value or ""
        try:
            self.current_path.write_text(buf, encoding="utf-8")
        except OSError as ex:
            self._snack(f"Save failed: {ex}")
            return
        self.last_saved_text = buf
        self._meaning_gen += 1
        self._margin_gen += 1
        self._rebuild_margin_slots()
        self._apply_slot_heights()
        self._refresh_title_bar()
        if not silent:
            self._snack("Saved.")

    def toggle_left(self, _e: ft.ControlEvent | None = None) -> None:
        self.left_open = not self.left_open
        self.left_panel.content = self._build_left_column()
        self.reflow_columns()
        self.left_panel.update()

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

    def _open_about(self, _e: ft.ControlEvent | None = None) -> None:
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("About Iterthink", weight=ft.FontWeight.W_600),
                content=ft.Text(
                    "Local Markdown writer with Ollama.\n\n"
                    "File → Settings: models, paths, appearance, and margin prompts.\n"
                    "Paragraph meaning hints use embeddings + optional LLM.\n\n"
                    "Ctrl+J / ⌘+J: KI-Panel rechts ein/aus.",
                    size=14,
                ),
                actions=[ft.TextButton("OK", on_click=lambda e: self.page.pop_dialog())],
                actions_alignment=ft.MainAxisAlignment.END,
            )
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
            value=md_source,
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
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
        return ft.MenuBar(
            expand=False,
            style=ft.MenuStyle(bgcolor=config.SURFACE_VARIANT),
            controls=[
                ft.SubmenuButton(
                    content=ft.Text("File"),
                    controls=[
                        ft.MenuItemButton(
                            content=ft.Text("Save"),
                            on_click=lambda e: self.page.run_task(self.save_file, e),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Settings…"),
                            on_click=lambda e: self.page.run_task(settings_ui.open_settings_dialog, self),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Quit"),
                            on_click=lambda e: self.page.run_task(self._win_close_async, e),
                        ),
                    ],
                ),
                ft.SubmenuButton(
                    content=ft.Text("View"),
                    controls=[
                        ft.MenuItemButton(
                            content=ft.Text("Toggle explorer"),
                            on_click=lambda e: self.toggle_left(e),
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("Toggle KI panel"),
                            on_click=lambda e: self.toggle_right(e),
                        ),
                    ],
                ),
                ft.SubmenuButton(
                    content=ft.Text("Help"),
                    controls=[
                        ft.MenuItemButton(
                            content=ft.Text("Help…"),
                            on_click=self._open_help,
                        ),
                        ft.MenuItemButton(
                            content=ft.Text("About"),
                            on_click=self._open_about,
                        ),
                    ],
                ),
            ],
        )

    def _pane_split_handle(
        self,
        *,
        tooltip: str,
        on_toggle: Callable[[ft.ControlEvent | None], None],
        hairline_after: bool,
    ) -> ft.Control:
        """Razor-thin edge + centered pill; pill highlights on hover; tap toggles pane."""
        hair = ft.BorderSide(1, ft.Colors.with_opacity(0.1, ft.Colors.WHITE))
        edge_border = ft.border.only(right=hair) if hairline_after else ft.border.only(left=hair)
        pill = ft.Container(
            width=4,
            height=40,
            border_radius=2,
            bgcolor=ft.Colors.TRANSPARENT,
        )

        def _on_strip_hover(e: ft.ControlEvent) -> None:
            pill.bgcolor = config.FEDORA_BLUE if e.data else ft.Colors.TRANSPARENT
            if _ctrl_on_page(pill):
                pill.update()

        strip = ft.Container(
            width=10,
            expand=True,
            alignment=ft.Alignment.CENTER,
            border=edge_border,
            tooltip=tooltip,
            content=pill,
            on_hover=_on_strip_hover,
        )
        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=lambda _e: on_toggle(None),
            content=strip,
        )

    def _explorer_collapse_handle_strip(self) -> ft.Control:
        """Right edge of tree card: thin hairline + hover pill; tap collapses."""
        return self._pane_split_handle(
            tooltip="Collapse explorer",
            on_toggle=self.toggle_left,
            hairline_after=False,
        )

    def _build_left_column(self) -> ft.Control:
        if not self.left_open:
            return ft.Row(
                [
                    ft.Container(expand=True),
                    self._pane_split_handle(
                        tooltip="Show explorer",
                        on_toggle=self.toggle_left,
                        hairline_after=True,
                    ),
                    ft.Container(expand=True),
                ],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        return ft.Column(
            [
                ft.Row(
                    [
                        self.tree_search_field,
                        self._tree_add_menu,
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                content=self.tree_column,
                                expand=True,
                                padding=4,
                            ),
                            self._explorer_collapse_handle_strip(),
                        ],
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                        spacing=0,
                    ),
                    expand=True,
                    border_radius=8,
                    bgcolor=config.SURFACE,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
            ],
            expand=True,
            spacing=8,
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
                            icon_color=ft.Colors.GREY_300,
                            on_click=self._win_minimize,
                        ),
                        ft.IconButton(
                            ft.Icons.CROP_SQUARE,
                            icon_size=18,
                            tooltip="Maximize",
                            icon_color=ft.Colors.GREY_300,
                            on_click=self._win_toggle_max,
                        ),
                        ft.IconButton(
                            ft.Icons.CLOSE,
                            icon_size=18,
                            tooltip="Close",
                            icon_color=ft.Colors.GREY_300,
                            on_click=lambda e: self.page.run_task(self._win_close_async, e),
                        ),
                    ],
                    spacing=0,
                )
                self._header_shell = ft.Container(
                    height=0,
                    opacity=0,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                    left=0,
                    right=0,
                    top=0,
                    bgcolor=config.SURFACE_VARIANT,
                    border=ft.border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_900)),
                    padding=ft.Padding.symmetric(horizontal=2, vertical=0),
                    on_hover=self._on_header_chrome_hover,
                    content=ft.Row(
                        [menu, drag, win_btns],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            else:
                self._header_shell = ft.Container(
                    height=0,
                    opacity=0,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
                    left=0,
                    right=0,
                    top=0,
                    bgcolor=config.SURFACE_VARIANT,
                    border=ft.border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_900)),
                    padding=ft.Padding.symmetric(horizontal=4, vertical=2),
                    on_hover=self._on_header_chrome_hover,
                    content=ft.Row([menu], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                )

        main_column_children.append(self._main_row)
        main_column = ft.Column(main_column_children, expand=True, spacing=0)

        stack_children: list[ft.Control] = [main_column]
        if not self.page.web and self._header_shell is not None:
            stack_children.append(
                ft.Container(
                    height=40,
                    left=0,
                    right=0,
                    top=0,
                    bgcolor=ft.Colors.with_opacity(0.001, ft.Colors.WHITE),
                    on_hover=self._on_header_strip_hover,
                )
            )
            stack_children.append(self._header_shell)

        self._rebuild_topic_pills()
        self._sync_ki_topic_buttons()
        self.reflow_columns()
        self._rebuild_margin_slots()
        self._apply_slot_heights()
        return ft.Stack(stack_children, expand=True, clip_behavior=ft.ClipBehavior.NONE)
