"""Flet UI: MarkdownStudio with Compose/Compare tabs and KI sidebar."""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import date
import re
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

import flet as ft
from flet.controls.types import PagePlatform
from ollama import AsyncClient

from iterthink import config, prompts, settings_ui, store_db, version_storage
from iterthink.prompts import TOPIC_CHANGE, TOPIC_DISCUSS, TOPIC_EVALUATE
from iterthink.compare_layout import pair_paragraphs_for_compare
from iterthink.diff_card import build_unified_spans
from iterthink.db.session import session_scope
from iterthink import paragraph_compare
from iterthink.margin import (
    distribute_heights,
    estimate_total_editor_height,
    paragraph_compose_slot_weights,
    paragraph_index_at_offset,
    replace_paragraph_at_index,
    split_paragraphs,
)
from iterthink.ollama_models import classify_installed_models
from iterthink.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.tree import build_md_tree, filter_md_tree

# Typing idle before autosave. Compare: left = latest Compose; right = draft / snapshot / AI. ✓/✗ refresh rows.
AUTOSAVE_IDLE_SEC = 6.0

# Collapsed side rails: wide enough to tap; square (no card rounding), transparent fill.
COLLAPSED_RAIL_WIDTH_PX = 36
# Expanded sidebars stay fixed width (not a fraction of window) so fullscreen does not widen them.
SIDEBAR_EXPANDED_WIDTH_PX = 280
# KI TabBarView needs a minimum height; actual height grows with measured pill rows.
KI_TAB_BODY_MIN_HEIGHT_PX = 96
# Vertical padding inside each tab page (above/below the pill row).
KI_TAB_PAGE_PAD_V_PX = 8
# Space between the tab labels strip and the pill area (inside the grey card).
KI_TAB_BAR_TO_PILLS_GAP_PX = 12
# Quick-action pills in the KI tab strip (text-only, compact).
KI_PILL_TEXT_SIZE = 10
READING_MAX_PX = 720
# Compose reading column uses this fraction of the *laid-out* compose width, capped at READING_MAX_PX.
COMPOSE_READING_WIDTH_FRAC = 0.92
_DIFF_SPAN_CHAR_CAP = 120_000

_COMPARE_KEY_DRAFT = "__draft__"
_COMPARE_KEY_AI = "__ai_preview__"
CompareCandidateSource = Literal["draft", "ai_preview", "snapshot"]

_COMPARE_COL_FONT_SIZE = 14
_COMPARE_COL_LINE_HEIGHT = 1.6
# Compose: sparkle strip — keeps paragraph column width unchanged vs prior layout.
_COMPOSE_MARGIN_COL_W = 104
# Compare row chrome (evaluate placeholder · status pill · actions); text columns still expand=1.
_COMPARE_EVAL_COL_W = 56
_COMPARE_PILL_COL_W = 72
# Compare action card: 2×2 grid; inner width = two equal columns; pad wraps the grid.
_COMPARE_ACTION_GRID_CELL = 40  # row height / half of inner width
_COMPARE_ACTION_INNER_W = _COMPARE_ACTION_GRID_CELL * 2
_COMPARE_ACTION_H_PAD = 5
_COMPARE_ACTION_V_PAD = 2
_COMPARE_ACTION_COL_W = _COMPARE_ACTION_INNER_W + 2 * _COMPARE_ACTION_H_PAD
# Compare row action card — row 2: play opens workflow site in the browser.
_PROJECT_PAGE_URL = "https://www.yourcompanyos.io"
_PROJECT_PAGE_TOOLTIP = "Start workflow on {yourcompany}os."

_HELP_MD_PATH = Path(__file__).resolve().parent / "help.md"

_TOPIC_MENU_LABEL: dict[str, str] = {
    TOPIC_DISCUSS: "Discuss",
    TOPIC_CHANGE: "Change",
    TOPIC_EVALUATE: "Evaluate",
}


# LLMs often ignore "no preamble"; strip common lead-ins before display / insert.
_CHANGE_REPLY_PREAMBLE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:here(?:'s|\s+is)\s+)?(?:the\s+)?(?:corrected|rewritten|revised|fixed|updated)\s+text\s*[.:：]?\s*"
    r"|here(?:'s|\s+is)\s+(?:your\s+)?(?:the\s+)?(?:corrected|rewritten)\s+(?:text|paragraph|version)\s*[.:：]?\s*"
    r"|\*\*(?:here(?:'s|\s+is)\s+)?(?:the\s+)?(?:corrected|rewritten)\s+text\*\*\s*[.:：]?\s*"
    r")+"
)


def _strip_change_topic_preamble(text: str) -> str:
    t = text.strip()
    for _ in range(4):
        nxt = _CHANGE_REPLY_PREAMBLE.sub("", t).strip()
        if nxt == t:
            return t
        t = nxt
    return t


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    """Flet raises RuntimeError when reading .page before the control is mounted."""
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def _compare_grid_slot(content: ft.Control, *, row_h: float, expand: bool) -> ft.Container:
    """One quadrant: equal flex width when expand=True; icon centered (fixes right-edge skew)."""
    return ft.Container(
        expand=expand,
        height=row_h,
        alignment=ft.Alignment.CENTER,
        content=content,
    )


def _ki_topic_index_for_prompt_topic(topic: str) -> int:
    """Map prompts.yaml margin action topic to KI tab strip index (Discuss / Change only)."""
    t = (topic or "").strip()
    if t == TOPIC_CHANGE:
        return 1
    # Discuss + evaluate topics use Discuss tab (Evaluate tab removed from sidebar).
    return 0


class MarkdownStudio:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._store_dir_resolved = config.STORE_DIR.resolve()
        self._fp_documents = ft.FilePicker()
        self._fp_store = ft.FilePicker()
        self._menu_bar: ft.MenuBar | None = None
        self.ollama = AsyncClient(host=config.OLLAMA_HOST) if config.OLLAMA_HOST else AsyncClient()
        self._db = store_db.connect()
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
        self._compare_diff_gen: int = 0
        self._main_tab_index: int = 0
        self._compare_candidate_source: CompareCandidateSource = "draft"
        self._compare_snapshot_version_id: int | None = None
        self._pending_ai_accept_action_id: str | None = None
        self._compare_right_fields: list[ft.TextField] = []
        self._compare_left_diff_texts: list[ft.Text] = []
        self._compare_row_pill_hosts: list[ft.Container] = []
        self._compare_row_stable_texts: list[str] = []
        self._compare_pill_gen: int = 0
        self._compare_refine_gen: int = 0
        # Compose text frozen when opening Compare (draft); left column diffs vs this, not live editor drift.
        self._compare_baseline_snapshot: str = ""
        self._compose_tab_inline_rename_active: bool = False
        self._compose_tab_rename_lock = asyncio.Lock()

        self._header_hide_gen: int = 0
        self._header_shell: ft.Container | None = None
        self._header_menu_open: int = 0
        self._header_chrome_hover: bool = False

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
        self._compare_editor = ft.TextField(
            multiline=True,
            max_lines=None,
            min_lines=1,
            visible=False,
            height=0,
            width=0,
            border=ft.InputBorder.NONE,
            filled=False,
            text_style=ft.TextStyle(font_family="monospace", size=14, height=1.6, color=ft.Colors.GREY_100),
            cursor_color=config.FEDORA_BLUE,
            selection_color=config.SELECTION_OVERLAY,
        )

        self._editor_shell = ft.Container(
            content=self.editor,
            expand=True,
        )

        self._compose_sparkle_column = ft.Column(spacing=0, tight=True, width=_COMPOSE_MARGIN_COL_W)
        self._compose_sparkle_roots: list[ft.Container] = []

        self._compose_reading_inner = ft.Row(
            [
                self._editor_shell,
                ft.Container(content=self._compose_sparkle_column, width=_COMPOSE_MARGIN_COL_W),
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        self._compose_reading_card = ft.Container(
            width=400,
            content=self._compose_reading_inner,
        )
        self._compose_reading_wrap = ft.Container(
            expand=True,
            alignment=ft.Alignment.TOP_CENTER,
            content=self._compose_reading_card,
            on_size_change=self._on_compose_reading_wrap_size,
        )
        self._compose_centered_row = ft.Row(
            [self._compose_reading_wrap],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        self._compose_tab_body = ft.Container(
            expand=True,
            padding=ft.padding.only(top=4, bottom=12),
            content=ft.Column(
                [self._compose_centered_row],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        )

        self._compare_candidate_dropdown = ft.Dropdown(
            width=260,
            dense=True,
            text_size=14,
            color=ft.Colors.GREY_200,
            bgcolor=ft.Colors.TRANSPARENT,
            filled=False,
            border=ft.InputBorder.NONE,
            border_width=0,
            content_padding=ft.padding.only(left=2, right=8, top=0, bottom=0),
            options=[ft.dropdown.Option(key=_COMPARE_KEY_DRAFT, text="Draft")],
            value=_COMPARE_KEY_DRAFT,
            disabled=True,
            tooltip="Draft, snapshot, or AI preview for the right column (left = latest Compose).",
            on_select=lambda e: self.page.run_task(self._on_compare_candidate_change_async, e),
        )
        self._compare_tab_label_row = ft.Row(
            [
                ft.Text("Comparison: ", size=14, color=ft.Colors.GREY_400),
                self._compare_candidate_dropdown,
            ],
            tight=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._compare_rows_listview = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
        )
        self._compare_editor_holder = ft.Container(content=self._compare_editor, visible=False, height=0)
        self._compare_tab_body = ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(content=self._compare_rows_listview, expand=True),
                    ],
                    expand=True,
                ),
                self._compare_editor_holder,
            ],
            expand=True,
            spacing=0,
        )

        self._compose_tab_filename_text = ft.Text(
            "—",
            size=14,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._compose_tab_filename_hit = ft.GestureDetector(
            content=self._compose_tab_filename_text,
            mouse_cursor=ft.MouseCursor.BASIC,
            tooltip="Open a note first",
            on_tap=self._on_compose_tab_filename_tap,
        )
        self._compose_tab_filename_field = ft.TextField(
            dense=True,
            text_size=14,
            max_lines=1,
            visible=False,
            width=220,
            filled=False,
            bgcolor=ft.Colors.TRANSPARENT,
            border=ft.InputBorder.UNDERLINE,
            border_width=1,
            border_color=ft.Colors.GREY_600,
            focused_border_color=config.FEDORA_BLUE,
            cursor_color=config.FEDORA_BLUE,
            selection_color=config.SELECTION_OVERLAY,
            content_padding=ft.padding.only(left=0, right=4, bottom=2, top=0),
            on_submit=self._on_compose_tab_rename_field_submit,
            on_blur=self._on_compose_tab_rename_field_blur,
        )
        self._compose_tab_filename_row = ft.Row(
            [self._compose_tab_filename_hit, self._compose_tab_filename_field],
            tight=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._compose_tab_label_row = ft.Row(
            [
                ft.Text("Compose: ", size=14, color=ft.Colors.GREY_400),
                self._compose_tab_filename_row,
            ],
            tight=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._main_tab_bar = ft.TabBar(
            tabs=[ft.Tab(label=self._compose_tab_label_row), ft.Tab(label=self._compare_tab_label_row)],
            scrollable=False,
            tab_alignment=ft.TabAlignment.FILL,
            indicator_color=config.FEDORA_BLUE,
            divider_color=ft.Colors.with_opacity(0.2, ft.Colors.GREY_700),
        )
        self._tab_bar_view = ft.TabBarView(
            controls=[self._compose_tab_body, self._compare_tab_body],
            expand=True,
        )
        self._sticky_tab_header = ft.Container(
            bgcolor=config.SURFACE,
            padding=ft.padding.only(bottom=2),
            content=self._main_tab_bar,
        )
        self._tabs_inner_column = ft.Column(
            [self._sticky_tab_header, self._tab_bar_view],
            expand=True,
            spacing=0,
        )
        self._main_tabs = ft.Tabs(
            content=self._tabs_inner_column,
            length=2,
            expand=True,
            selected_index=0,
            on_change=self._on_main_tabs_change,
        )

        self.sheet_scroll = ft.Column(
            controls=[self._main_tabs],
            expand=True,
        )

        self.app_symbol = ft.Image(
            src=str(config.APP_SYMBOL_PNG),
            width=22,
            height=22,
            fit=ft.BoxFit.CONTAIN,
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
            content=ft.Row(
                [self.app_symbol, self.filename_text, self.dirty_dot],
                tight=True,
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
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
            width=SIDEBAR_EXPANDED_WIDTH_PX,
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

        self._pill_row_discuss = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._pill_row_change = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._ki_tab_body_heights: list[float] = [
            float(KI_TAB_BODY_MIN_HEIGHT_PX),
            float(KI_TAB_BODY_MIN_HEIGHT_PX),
        ]

        self._ki_tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label="Discuss"),
                ft.Tab(label="Change"),
            ],
            scrollable=False,
            tab_alignment=ft.TabAlignment.FILL,
            indicator_color=config.FEDORA_BLUE,
            divider_color=ft.Colors.with_opacity(0.2, ft.Colors.GREY_700),
        )
        self._ki_tab_bar_view = ft.TabBarView(
            controls=[
                ft.Container(
                    padding=ft.padding.symmetric(
                        horizontal=4,
                        vertical=KI_TAB_PAGE_PAD_V_PX,
                    ),
                    content=self._pill_row_discuss,
                ),
                ft.Container(
                    padding=ft.padding.symmetric(
                        horizontal=4,
                        vertical=KI_TAB_PAGE_PAD_V_PX,
                    ),
                    content=self._pill_row_change,
                ),
            ],
            height=float(KI_TAB_BODY_MIN_HEIGHT_PX + 2 * KI_TAB_PAGE_PAD_V_PX),
        )
        self._ki_sticky_tab_header = ft.Container(
            bgcolor=ft.Colors.TRANSPARENT,
            padding=ft.padding.only(bottom=2),
            content=self._ki_tab_bar,
        )
        self._ki_tabs_inner_column = ft.Column(
            [self._ki_sticky_tab_header, self._ki_tab_bar_view],
            spacing=KI_TAB_BAR_TO_PILLS_GAP_PX,
        )
        self._ki_topic_tabs = ft.Tabs(
            content=self._ki_tabs_inner_column,
            length=2,
            selected_index=0,
            on_change=self._on_ki_tabs_change,
        )
        self._ki_topic_shell = ft.Container(
            bgcolor=config.SURFACE,
            border_radius=8,
            padding=ft.padding.all(10),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=self._ki_topic_tabs,
        )

        self._chat_history = ft.ListView(
            expand=True,
            spacing=8,
            padding=ft.padding.all(8),
            auto_scroll=True,
        )
        self._chat_input = ft.TextField(
            hint_text="Ask",
            min_lines=1,
            max_lines=4,
            multiline=True,
            dense=True,
            filled=True,
            bgcolor=config.SURFACE,
            border_radius=8,
            expand=True,
            text_size=12,
            border_color=ft.Colors.GREY_700,
            focused_border_color=config.FEDORA_BLUE,
            cursor_color=config.FEDORA_BLUE,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=6),
            on_submit=lambda e: self.page.run_task(self._send_chat_message, e),
        )
        self._chat_send_btn = ft.IconButton(
            icon=ft.Icons.SEND,
            icon_size=20,
            tooltip="Send",
            icon_color=config.FEDORA_BLUE,
            style=ft.ButtonStyle(padding=ft.padding.all(4)),
            on_click=lambda e: self.page.run_task(self._send_chat_message, e),
        )
        self._chat_model_options: list[str] = []
        self._chat_model_btn = ft.IconButton(
            icon=ft.Icons.SETTINGS,
            icon_size=20,
            icon_color=ft.Colors.GREY_400,
            tooltip=self._chat_model_tooltip(),
            style=ft.ButtonStyle(padding=ft.padding.all(4)),
            on_click=lambda e: self.page.run_task(settings_ui.open_settings_dialog, self),
        )
        self._chat_composer = ft.Container(
            padding=ft.padding.all(8),
            border_radius=16,
            bgcolor=ft.Colors.with_opacity(0.35, ft.Colors.BLACK),
            border=ft.border.all(1, ft.Colors.with_opacity(0.45, ft.Colors.GREY_700)),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(
                [self._chat_model_btn, self._chat_input, self._chat_send_btn],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
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
                    self._chat_composer,
                ],
                expand=True,
                spacing=8,
            ),
        )

        self._right_ki_column = ft.Column(
            [
                self._ki_topic_shell,
                self._right_chat_section,
            ],
            expand=True,
            spacing=8,
        )

        self._pill_row_discuss.on_size_change = self._on_ki_pill_row_size_discuss
        self._pill_row_change.on_size_change = self._on_ki_pill_row_size_change

        self.right_panel = ft.Container(
            width=SIDEBAR_EXPANDED_WIDTH_PX,
            margin=12,
            padding=8,
            bgcolor=config.SIDEBAR_SURFACE,
            border_radius=15,
            content=ft.Container(),
            animate=ft.Animation(300, ft.AnimationCurve.DECELERATE),
        )

        self.page.on_keyboard_event = self._on_page_keyboard

    def _on_ki_tabs_change(self, e: ft.ControlEvent) -> None:
        try:
            self._ki_topic_index = int(e.data)
        except (TypeError, ValueError):
            self._ki_topic_index = int(self._ki_topic_tabs.selected_index)

    def _set_ki_topic(self, index: int) -> None:
        ix = max(0, min(1, int(index)))
        self._ki_topic_index = ix
        if self._ki_topic_tabs.selected_index != ix:
            self._ki_topic_tabs.selected_index = ix
        if _ctrl_on_page(self._ki_topic_tabs):
            self._ki_topic_tabs.update()

    def _on_ki_pill_row_size_discuss(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._ki_tab_body_heights[0] = max(float(e.height), 28.0)
        self._apply_ki_tab_bar_view_height()

    def _on_ki_pill_row_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._ki_tab_body_heights[1] = max(float(e.height), 28.0)
        self._apply_ki_tab_bar_view_height()

    def _apply_ki_tab_bar_view_height(self) -> None:
        inner = max(
            self._ki_tab_body_heights[0],
            self._ki_tab_body_heights[1],
            float(KI_TAB_BODY_MIN_HEIGHT_PX),
        )
        h = inner + 2 * float(KI_TAB_PAGE_PAD_V_PX)
        cur = float(self._ki_tab_bar_view.height or 0)
        if abs(cur - h) < 0.75:
            return
        self._ki_tab_bar_view.height = h
        if _ctrl_on_page(self._ki_tab_bar_view):
            self._ki_tab_bar_view.update()

    async def _defer_sync_ki_tab_height(self) -> None:
        await asyncio.sleep(0.06)
        self._apply_ki_tab_bar_view_height()

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
                    self._pane_split_handle(
                        tooltip="Show KI panel",
                        on_toggle=self.toggle_right,
                        hairline_after=False,
                        compact_rail=True,
                    ),
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
            actions = sorted(
                prompts.actions_for_topic(topic),
                key=lambda a: (a.label or "").casefold(),
            )
            for a in actions:
                label = a.label
                aid = a.id
                row.controls.append(
                    ft.FilledButton(
                        content=label,
                        elevation=0,
                        style=ft.ButtonStyle(
                            text_style=ft.TextStyle(size=KI_PILL_TEXT_SIZE),
                            visual_density=ft.VisualDensity.COMPACT,
                            padding=ft.padding.symmetric(horizontal=6, vertical=3),
                        ),
                        on_click=lambda e, action_id=aid: self.page.run_task(self._quick_margin_action, action_id),
                    )
                )

        fill_row(self._pill_row_discuss, TOPIC_DISCUSS)
        fill_row(self._pill_row_change, TOPIC_CHANGE)

        for row in (self._pill_row_discuss, self._pill_row_change):
            if _ctrl_on_page(row):
                row.update()

        self.page.run_task(self._defer_sync_ki_tab_height)

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
        elif self._header_menu_open == 0:
            self._schedule_header_hide()

    def _on_header_chrome_hover(self, e: ft.ControlEvent) -> None:
        if self.page.web or self._header_shell is None:
            return
        self._header_chrome_hover = bool(e.data)
        if e.data:
            self._invalidate_header_hide()
        elif self._header_menu_open == 0:
            self._schedule_header_hide()

    def _on_page_keyboard(self, e: ft.KeyboardEvent) -> None:
        key = (e.key or "").lower()
        if key == "escape" and self._compose_tab_inline_rename_active:
            self.page.run_task(self._compose_tab_cancel_inline_rename)
            return
        if key != "j":
            return
        if not (e.ctrl or e.meta):
            return
        self.toggle_right()

    async def _quick_margin_action(self, action_id: str) -> None:
        buf = self.editor.value or ""
        tf = self.editor
        sel = tf.selection
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
            content=ft.Text(text, size=12, selectable=True, color=ft.Colors.GREY_100),
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

        doc = (self._editor_buffer() or "")[:8000]
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
        reply = ft.Text("", size=12, selectable=True, color=ft.Colors.GREY_100)
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
        left_w = SIDEBAR_EXPANDED_WIDTH_PX if self.left_open else COLLAPSED_RAIL_WIDTH_PX
        right_w = SIDEBAR_EXPANDED_WIDTH_PX if self.right_open else COLLAPSED_RAIL_WIDTH_PX
        self.left_panel.width = left_w
        self.right_panel.width = right_w
        self._sync_side_panel_chrome()
        self._margin_gen += 1
        self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)
        if self._main_tab_index == 1:
            self._compare_diff_gen += 1
            self.page.run_task(self._debounced_compare_diff, self._compare_diff_gen)
        self.page.update()

    def _working_document_text(self) -> str:
        """Text compared to on-disk `last_saved_text` for dirty + save (Compose or Compare draft)."""
        if self._main_tab_index == 1 and self._compare_candidate_source == "draft":
            return self._compare_editor.value or ""
        return self.editor.value or ""

    def _is_dirty(self) -> bool:
        return self._working_document_text() != self.last_saved_text

    def _editor_buffer(self) -> str:
        if self._main_tab_index == 1:
            return self._compare_editor.value or ""
        return self.editor.value or ""

    def _refresh_compare_tab_candidate_ui(self) -> None:
        opts: list[ft.dropdown.Option] = [ft.dropdown.Option(key=_COMPARE_KEY_DRAFT, text="Draft")]
        if self._compare_candidate_source == "ai_preview" and self._pending_ai_accept_action_id:
            act = prompts.get_margin_action(self._pending_ai_accept_action_id)
            ai_text = f"{act.label} · preview" if act else "AI · preview"
            opts.append(ft.dropdown.Option(key=_COMPARE_KEY_AI, text=ai_text))
        if self.current_path:
            with session_scope() as s:
                snaps = version_storage.list_snapshots(s, self.current_path.resolve())
            for sn in snaps:
                opts.append(
                    ft.dropdown.Option(
                        key=str(sn.version_id),
                        text=version_storage.snapshot_display_text(sn),
                    )
                )
        self._compare_candidate_dropdown.options = opts
        keys_ok = {o.key for o in opts}
        if self._compare_candidate_source == "draft":
            self._compare_candidate_dropdown.value = _COMPARE_KEY_DRAFT
        elif self._compare_candidate_source == "ai_preview":
            self._compare_candidate_dropdown.value = (
                _COMPARE_KEY_AI if _COMPARE_KEY_AI in keys_ok else _COMPARE_KEY_DRAFT
            )
        elif self._compare_candidate_source == "snapshot" and self._compare_snapshot_version_id is not None:
            sk = str(self._compare_snapshot_version_id)
            self._compare_candidate_dropdown.value = sk if sk in keys_ok else _COMPARE_KEY_DRAFT
        else:
            self._compare_candidate_dropdown.value = _COMPARE_KEY_DRAFT
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()

    def _sync_version_toolbar_state(self) -> None:
        has_doc = self.current_path is not None
        self._compare_candidate_dropdown.disabled = not has_doc
        self._compare_candidate_dropdown.tooltip = (
            "Pick draft, history, or AI preview for the right column."
            if has_doc
            else "Open a markdown file from the tree to list versions."
        )
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()

    async def _on_compare_candidate_change_async(self, e: ft.ControlEvent) -> None:
        if self._compare_candidate_dropdown.disabled or not self.current_path:
            return
        v = e.control.value
        if v is None or v == _COMPARE_KEY_DRAFT:
            self._compare_candidate_source = "draft"
            self._compare_snapshot_version_id = None
            self._pending_ai_accept_action_id = None
            self._compare_editor.value = self.editor.value or ""
            self._capture_compare_baseline_snapshot()
            if _ctrl_on_page(self._compare_editor):
                self._compare_editor.update()
        elif v == _COMPARE_KEY_AI:
            return
        else:
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            self._compare_candidate_source = "snapshot"
            self._compare_snapshot_version_id = vid
            self._pending_ai_accept_action_id = None
            try:
                with session_scope() as s:
                    body = version_storage.load_version_body(s, vid)
                self._compare_editor.value = body
                if _ctrl_on_page(self._compare_editor):
                    self._compare_editor.update()
            except BaseException:
                self._snack("Could not load that version.")
                return
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

    def _on_main_tabs_change(self, e: ft.ControlEvent) -> None:
        try:
            new_ix = int(e.data)
        except (TypeError, ValueError):
            new_ix = int(self._main_tabs.selected_index)
        self.page.run_task(self._sync_tab_switch_async, new_ix)

    async def _sync_tab_switch_async(self, new_ix: int) -> None:
        prev = self._main_tab_index
        if new_ix == prev:
            return
        if prev == 0 and new_ix == 1:
            if self._compare_candidate_source == "draft":
                self._compare_editor.value = self.editor.value or ""
                self._capture_compare_baseline_snapshot()
            self._rebuild_compare_paragraph_ui()
        elif prev == 1 and new_ix == 0:
            if self._compare_candidate_source == "draft":
                self._sync_compare_buffer_from_fields()
                self.editor.value = self._compare_editor.value or ""
                if _ctrl_on_page(self.editor):
                    self.editor.update()
        self._main_tab_index = new_ix
        if new_ix == 0:
            self._margin_gen += 1
            await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_title_bar()

    def _compare_para_text_style(self) -> ft.TextStyle:
        return ft.TextStyle(
            font_family="monospace",
            size=_COMPARE_COL_FONT_SIZE,
            height=_COMPARE_COL_LINE_HEIGHT,
            color=ft.Colors.GREY_100,
        )

    def _compare_pill_colors(self, kind: paragraph_compare.SlotKind) -> tuple[str, str]:
        m: dict[str, tuple[str, str]] = {
            "unchanged": (ft.Colors.with_opacity(0.14, ft.Colors.GREY_500), ft.Colors.GREY_400),
            "minor": (ft.Colors.with_opacity(0.28, ft.Colors.BLUE_400), ft.Colors.BLUE_100),
            "major": (ft.Colors.with_opacity(0.28, ft.Colors.ORANGE_400), ft.Colors.ORANGE_100),
            "rewritten": (ft.Colors.with_opacity(0.28, ft.Colors.PURPLE_400), ft.Colors.PURPLE_100),
            "new": (ft.Colors.with_opacity(0.28, ft.Colors.GREEN_400), ft.Colors.GREEN_100),
            "deleted": (ft.Colors.with_opacity(0.28, ft.Colors.RED_400), ft.Colors.RED_100),
        }
        return m.get(kind, (ft.Colors.with_opacity(0.2, ft.Colors.GREY_600), ft.Colors.GREY_200))

    def _make_compare_pill(self, kind: paragraph_compare.SlotKind) -> ft.Container:
        label = paragraph_compare.slot_kind_label(kind)
        bg, fg = self._compare_pill_colors(kind)
        return ft.Container(
            content=ft.Text(
                label,
                size=10,
                weight=ft.FontWeight.W_600,
                color=fg,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                text_align=ft.TextAlign.CENTER,
            ),
            bgcolor=bg,
            padding=ft.padding.symmetric(horizontal=5, vertical=3),
            border_radius=10,
            alignment=ft.Alignment.CENTER,
        )

    def _sync_compare_buffer_from_fields(self) -> None:
        parts = [tf.value or "" for tf in self._compare_right_fields]
        self._compare_editor.value = "\n\n".join(parts) if parts else ""

    def _capture_compare_baseline_snapshot(self) -> None:
        """Store Compose buffer as the Compare left baseline (draft mode only uses this via _compare_latest_baseline_text)."""
        self._compare_baseline_snapshot = self.editor.value or ""

    def _compare_latest_baseline_text(self) -> str:
        """Baseline for the left diff column: frozen snapshot while editing Compare draft; else live Compose."""
        if self._main_tab_index == 1 and self._compare_candidate_source == "draft":
            return self._compare_baseline_snapshot
        return self.editor.value or ""

    def _persist_ai_accept_snapshots(
        self,
        pre_buf: str,
        post_buf: str,
        *,
        para_index: int,
        action_id: str,
    ) -> None:
        """Two DB snapshots for AI Compare accept: document before merge, then after (parent chain)."""
        if not self.current_path:
            return
        act = prompts.get_margin_action(action_id)
        apply_label = act.label if act else action_id
        before_label = f"Before accept · paragraph {para_index + 1}"
        try:
            with session_scope() as s:
                rp = self.current_path.resolve()
                version_storage.persist_version_snapshot(
                    s,
                    rp,
                    pre_buf,
                    "before_apply",
                    display_label=before_label,
                )
                version_storage.persist_version_snapshot(
                    s,
                    rp,
                    post_buf,
                    "ai_apply",
                    display_label=apply_label,
                )
        except BaseException:
            pass

    def _compare_paragraph_diff_spans(self, left_para: str, right_para: str) -> list[ft.TextSpan]:
        """Inline diff for left column: strikethrough removals, green additions vs right."""
        cap = 80_000
        lp, rp = left_para, right_para
        if len(lp) + len(rp) > cap:
            lp = lp[: cap // 2] + "\n…"
            rp = rp[: cap // 2] + "\n…"
        return build_unified_spans(
            lp,
            rp,
            base_size=_COMPARE_COL_FONT_SIZE,
            base_color=ft.Colors.GREY_100,
            font_family="monospace",
            insert_color=ft.Colors.LIGHT_GREEN_200,
        )

    def _refresh_compare_left_diff_spans(self) -> None:
        """Update inline diff in the left column when the candidate text edits (same paragraph count)."""
        if len(self._compare_left_diff_texts) != len(self._compare_right_fields):
            return
        baseline = self._compare_latest_baseline_text()
        candidate = self._compare_editor.value or ""
        if len(baseline) + len(candidate) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            baseline = baseline[:half] + "\n…"
            candidate = candidate[:half] + "\n…"
        pairs = pair_paragraphs_for_compare(baseline, candidate)
        for i, (left_txt, right_txt) in enumerate(pairs):
            if i >= len(self._compare_left_diff_texts):
                break
            t = self._compare_left_diff_texts[i]
            t.spans = self._compare_paragraph_diff_spans(left_txt, right_txt)
            if _ctrl_on_page(t):
                t.update()

    def _rebuild_compare_paragraph_ui(self) -> None:
        self._compare_pill_gen += 1
        baseline = self._compare_latest_baseline_text()
        candidate = self._compare_editor.value or ""
        if len(baseline) + len(candidate) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            baseline = baseline[:half] + "\n…"
            candidate = candidate[:half] + "\n…"
        pairs = pair_paragraphs_for_compare(baseline, candidate)
        self._compare_row_stable_texts = [left for left, _ in pairs]
        kinds_h = paragraph_compare.slot_kinds_heuristic(baseline, candidate)

        self._compare_rows_listview.controls.clear()
        self._compare_right_fields.clear()
        self._compare_row_pill_hosts.clear()
        self._compare_left_diff_texts.clear()

        para_style = self._compare_para_text_style()
        shared_tf_kwargs: dict[str, Any] = {
            "multiline": True,
            "max_lines": None,
            "min_lines": 1,
            "border": ft.InputBorder.NONE,
            "filled": False,
            "dense": True,
            "text_size": _COMPARE_COL_FONT_SIZE,
            "text_style": para_style,
            "cursor_color": config.FEDORA_BLUE,
            "selection_color": config.SELECTION_OVERLAY,
            "content_padding": ft.padding.all(8),
        }

        show_actions = bool(self.current_path)
        _compare_row_icon_style = ft.ButtonStyle(
            padding=ft.padding.symmetric(horizontal=2, vertical=1),
            visual_density=ft.VisualDensity.COMPACT,
        )
        for i, (left_txt, right_txt) in enumerate(pairs):
            kind = kinds_h[i] if i < len(kinds_h) else "unchanged"
            pill = self._make_compare_pill(kind)
            pill_host = ft.Container(
                content=pill,
                width=_COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4),
            )
            eval_cell = ft.Container(
                width=_COMPARE_EVAL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4, right=2),
                content=ft.Container(),
            )

            left_diff = ft.Text(
                spans=self._compare_paragraph_diff_spans(left_txt, right_txt),
                selectable=True,
            )
            self._compare_left_diff_texts.append(left_diff)
            left_cell = ft.Container(
                content=left_diff,
                expand=1,
                padding=ft.padding.all(8),
            )
            right_tf = ft.TextField(
                **shared_tf_kwargs,
                value=right_txt,
                read_only=False,
                enable_interactive_selection=True,
                hint_text="…",
                on_change=lambda _e, ix=i: self._on_compare_para_field_change(ix),
            )

            actions_ctrl: ft.Control
            actions_hover_wrap: ft.Container | None = None
            if show_actions:
                spark = self._paragraph_sparkle_menu_control(i, for_compare=True, compact=True)
                row_h = _COMPARE_ACTION_GRID_CELL
                inner_w = _COMPARE_ACTION_INNER_W
                actions_inner = ft.Container(
                    bgcolor=ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
                    border=ft.border.all(1, ft.Colors.with_opacity(0.45, ft.Colors.GREY_600)),
                    border_radius=8,
                    padding=ft.padding.symmetric(
                        horizontal=_COMPARE_ACTION_H_PAD,
                        vertical=_COMPARE_ACTION_V_PAD,
                    ),
                    content=ft.Container(
                        width=inner_w,
                        content=ft.Column(
                            [
                                ft.Container(
                                    width=inner_w,
                                    content=ft.Row(
                                        [
                                            _compare_grid_slot(
                                                ft.IconButton(
                                                    ft.Icons.CHECK_ROUNDED,
                                                    icon_size=14,
                                                    icon_color=config.FEDORA_BLUE,
                                                    tooltip="Apply this paragraph to the document",
                                                    style=_compare_row_icon_style,
                                                    on_click=lambda _e, ix=i: self.page.run_task(
                                                        self._compare_accept_paragraph_async, ix
                                                    ),
                                                ),
                                                row_h=row_h,
                                                expand=True,
                                            ),
                                            _compare_grid_slot(
                                                ft.IconButton(
                                                    ft.Icons.CLOSE_ROUNDED,
                                                    icon_size=14,
                                                    icon_color=ft.Colors.GREY_400,
                                                    tooltip="Reset this paragraph to match latest (left)",
                                                    style=_compare_row_icon_style,
                                                    on_click=lambda _e, ix=i: self.page.run_task(
                                                        self._compare_decline_paragraph_async, ix
                                                    ),
                                                ),
                                                row_h=row_h,
                                                expand=True,
                                            ),
                                        ],
                                        spacing=0,
                                    ),
                                ),
                                ft.Container(
                                    width=inner_w,
                                    content=ft.Row(
                                        [
                                            _compare_grid_slot(
                                                ft.IconButton(
                                                    ft.Icons.PLAY_ARROW,
                                                    icon_size=14,
                                                    icon_color=config.FEDORA_BLUE,
                                                    tooltip=_PROJECT_PAGE_TOOLTIP,
                                                    style=_compare_row_icon_style,
                                                    on_click=lambda _e: self.page.run_task(self._open_project_page),
                                                ),
                                                row_h=row_h,
                                                expand=True,
                                            ),
                                            _compare_grid_slot(
                                                spark,
                                                row_h=row_h,
                                                expand=True,
                                            ),
                                        ],
                                        spacing=0,
                                    ),
                                ),
                            ],
                            spacing=0,
                            tight=True,
                        ),
                    ),
                )
                actions_hover_wrap = ft.Container(
                    content=actions_inner,
                    opacity=0.0,
                    animate_opacity=180,
                    alignment=ft.Alignment.TOP_CENTER,
                )
                actions_ctrl = actions_hover_wrap
            else:
                actions_ctrl = ft.Container(width=_COMPARE_ACTION_COL_W)

            row_inner = ft.Row(
                [
                    eval_cell,
                    left_cell,
                    pill_host,
                    ft.Container(right_tf, expand=1),
                    ft.Container(
                        content=actions_ctrl,
                        width=_COMPARE_ACTION_COL_W,
                        alignment=ft.Alignment.TOP_CENTER,
                        padding=ft.padding.only(top=4),
                    ),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            row: ft.Control = (
                ft.Container(
                    content=row_inner,
                    on_hover=lambda e, w=actions_hover_wrap: self._on_compare_row_hover(e, w),
                )
                if actions_hover_wrap is not None
                else row_inner
            )
            self._compare_rows_listview.controls.append(row)
            self._compare_right_fields.append(right_tf)
            self._compare_row_pill_hosts.append(pill_host)

        self._sync_compare_buffer_from_fields()
        if _ctrl_on_page(self._compare_rows_listview):
            self._compare_rows_listview.update()

        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _refresh_compare_diff_immediate(self) -> None:
        self._rebuild_compare_paragraph_ui()

    async def _debounced_compare_diff(self, gen: int) -> None:
        await asyncio.sleep(0.12)
        if gen != self._compare_diff_gen:
            return
        cand = self._compare_editor.value or ""
        n_para = len(split_paragraphs(cand))
        if n_para != len(self._compare_right_fields):
            self._rebuild_compare_paragraph_ui()
            return
        self._refresh_compare_left_diff_spans()
        self._compare_pill_gen += 1
        pg = self._compare_pill_gen
        self.page.run_task(self._debounced_compare_pill_refresh, pg)

    async def _debounced_compare_pill_refresh(self, gen: int) -> None:
        await asyncio.sleep(0.18)
        if gen != self._compare_pill_gen:
            return
        if self._main_tab_index != 1:
            return
        baseline = self._compare_latest_baseline_text()
        candidate = self._compare_editor.value or ""
        kinds = paragraph_compare.slot_kinds_heuristic(baseline, candidate)
        for i, host in enumerate(self._compare_row_pill_hosts):
            k = kinds[i] if i < len(kinds) else "unchanged"
            host.content = self._make_compare_pill(k)
            if _ctrl_on_page(host):
                host.update()

    async def _debounced_refine_compare_slots(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._compare_refine_gen:
            return
        if self._main_tab_index != 1:
            return
        baseline = self._compare_latest_baseline_text()
        candidate = self._compare_editor.value or ""
        if not baseline.strip() and not candidate.strip():
            return
        try:
            refined = await paragraph_compare.classify_slots_async(
                self.ollama,
                chat_model=self.ollama_model,
                embed_model=self.ollama_embed_model,
                baseline_text=baseline,
                new_text=candidate,
            )
        except BaseException:
            return
        if gen != self._compare_refine_gen:
            return
        for i, host in enumerate(self._compare_row_pill_hosts):
            k = refined[i] if i < len(refined) else "unchanged"
            host.content = self._make_compare_pill(k)
            if _ctrl_on_page(host):
                host.update()

    def _on_compare_para_field_change(self, index: int) -> None:
        self._sync_compare_buffer_from_fields()
        cand = self._compare_editor.value or ""
        n_para = len(split_paragraphs(cand))
        if n_para != len(self._compare_right_fields):
            self._rebuild_compare_paragraph_ui()
            self._refresh_title_bar()
            if self._compare_candidate_source == "draft" and self.current_path:
                self._autosave_gen += 1
                self.page.run_task(self._autosave_after_idle, self._autosave_gen)
            return
        self._refresh_title_bar()
        self._compare_diff_gen += 1
        dgen = self._compare_diff_gen
        self.page.run_task(self._debounced_compare_diff, dgen)
        if self._compare_candidate_source != "draft":
            return
        if not self.current_path:
            return
        self._autosave_gen += 1
        agen = self._autosave_gen
        self.page.run_task(self._autosave_after_idle, agen)

    async def _compare_accept_paragraph_async(self, index: int) -> None:
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if index < 0 or index >= len(self._compare_right_fields):
            return
        cand_para = self._compare_right_fields[index].value or ""
        pre_buf = self.editor.value or ""
        new_buf = replace_paragraph_at_index(pre_buf, index, cand_para)
        ai_flow = (
            self._compare_candidate_source == "ai_preview"
            and self._pending_ai_accept_action_id
        )
        if ai_flow:
            self._persist_ai_accept_snapshots(
                pre_buf,
                new_buf,
                para_index=index,
                action_id=self._pending_ai_accept_action_id,
            )
            try:
                self.current_path.write_text(new_buf, encoding="utf-8")
            except OSError as ex:
                self._snack(f"Save failed: {ex}")
                return
            self.last_saved_text = new_buf
            self._refresh_compare_tab_candidate_ui()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()
        self.editor.value = new_buf
        self._capture_compare_baseline_snapshot()
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._rebuild_compare_paragraph_ui()
        self._refresh_title_bar()
        self._snack(f"Paragraph {index + 1} applied to the document.")

    async def _compare_decline_paragraph_async(self, index: int) -> None:
        if index < 0 or index >= len(self._compare_right_fields):
            return
        if index < len(self._compare_row_stable_texts):
            revert = self._compare_row_stable_texts[index]
        else:
            paras = split_paragraphs(self._compare_latest_baseline_text() or "")
            revert = paras[index] if 0 <= index < len(paras) else ""
        self._compare_right_fields[index].value = revert
        if _ctrl_on_page(self._compare_right_fields[index]):
            self._compare_right_fields[index].update()
        self._sync_compare_buffer_from_fields()
        self._rebuild_compare_paragraph_ui()
        self._refresh_title_bar()

    def _hide_prompt_footer(self, footer: ft.Row) -> None:
        footer.controls.clear()
        footer.visible = False
        if _ctrl_on_page(footer):
            footer.update()

    async def _stage_ai_candidate_async(self, idx: int, reply: ft.Text, footer: ft.Row, action_id: str) -> None:
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        act = prompts.get_margin_action(action_id)
        if self.current_path:
            try:
                with session_scope() as s:
                    version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        self.editor.value or "",
                        "ai_staged",
                        display_label=f"{act.label} · preview" if act else "AI · preview",
                    )
            except BaseException:
                pass
        base = self.editor.value or ""
        self._compare_editor.value = replace_paragraph_at_index(base, idx, text)
        self._compare_candidate_source = "ai_preview"
        self._compare_snapshot_version_id = None
        self._pending_ai_accept_action_id = action_id
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._main_tabs.selected_index = 1
        self._main_tab_index = 1
        self._refresh_compare_tab_candidate_ui()
        self._compare_candidate_dropdown.value = _COMPARE_KEY_AI
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

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
        self._refresh_compose_tab_label()

    def _refresh_compose_tab_label(self) -> None:
        if self._compose_tab_inline_rename_active:
            return
        if not self.current_path:
            self._compose_tab_filename_text.value = "—"
            self._compose_tab_filename_text.color = ft.Colors.GREY_500
            self._compose_tab_filename_text.style = None
            self._compose_tab_filename_hit.mouse_cursor = ft.MouseCursor.BASIC
            self._compose_tab_filename_hit.tooltip = "Open a note first"
        else:
            self._compose_tab_filename_text.value = self.current_path.name
            self._compose_tab_filename_text.color = ft.Colors.GREY_200
            self._compose_tab_filename_text.style = None
            self._compose_tab_filename_hit.mouse_cursor = ft.MouseCursor.CLICK
            self._compose_tab_filename_hit.tooltip = "Click to rename"
        if _ctrl_on_page(self._compose_tab_filename_text):
            self._compose_tab_filename_text.update()
        if _ctrl_on_page(self._compose_tab_filename_hit):
            self._compose_tab_filename_hit.update()

    def _compose_tab_exit_rename_mode(self) -> None:
        self._compose_tab_inline_rename_active = False
        self._compose_tab_filename_hit.visible = True
        self._compose_tab_filename_field.visible = False
        self._refresh_compose_tab_label()
        if _ctrl_on_page(self._compose_tab_filename_hit):
            self._compose_tab_filename_hit.update()
        if _ctrl_on_page(self._compose_tab_filename_field):
            self._compose_tab_filename_field.update()
        if _ctrl_on_page(self._compose_tab_filename_row):
            self._compose_tab_filename_row.update()

    def _on_compose_tab_filename_tap(self, _e: ft.ControlEvent) -> None:
        if not self.current_path:
            self._snack("Open or create a note first.")
            return
        self.page.run_task(self._compose_tab_begin_inline_rename_async)

    async def _compose_tab_begin_inline_rename_async(self) -> None:
        async with self._compose_tab_rename_lock:
            if not self.current_path:
                return
            if self._compose_tab_inline_rename_active:
                return
            self._compose_tab_inline_rename_active = True
            self._compose_tab_filename_field.value = self.current_path.name
            self._compose_tab_filename_hit.visible = False
            self._compose_tab_filename_field.visible = True
            if _ctrl_on_page(self._compose_tab_filename_field):
                self._compose_tab_filename_field.update()
            if _ctrl_on_page(self._compose_tab_filename_hit):
                self._compose_tab_filename_hit.update()
            if _ctrl_on_page(self._compose_tab_filename_row):
                self._compose_tab_filename_row.update()
        await asyncio.sleep(0.05)
        self._compose_tab_filename_field.focus()

    def _on_compose_tab_rename_field_submit(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._compose_tab_commit_inline_rename)

    def _on_compose_tab_rename_field_blur(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._compose_tab_commit_inline_rename)

    async def _compose_tab_cancel_inline_rename(self) -> None:
        async with self._compose_tab_rename_lock:
            if not self._compose_tab_inline_rename_active:
                return
            self._compose_tab_exit_rename_mode()

    async def _compose_tab_commit_inline_rename(self) -> None:
        async with self._compose_tab_rename_lock:
            if not self._compose_tab_inline_rename_active:
                return
            old = self.current_path
            if not old:
                self._compose_tab_exit_rename_mode()
                return
            name = (self._compose_tab_filename_field.value or "").strip()
            if not name or name in (".", ".."):
                self._snack("Invalid filename.")
                self._compose_tab_exit_rename_mode()
                return
            if "/" in name or "\\" in name or "\x00" in name:
                self._snack("Use a single filename only.")
                self._compose_tab_exit_rename_mode()
                return
            new_path = old.parent / name
            if new_path.resolve() == old.resolve():
                self._compose_tab_exit_rename_mode()
                return
            if new_path.exists():
                self._snack("A file or folder with that name already exists.")
                self._compose_tab_exit_rename_mode()
                return
            old_r = old.resolve()
            new_r = new_path.resolve()
            try:
                old.rename(new_path)
            except OSError as ex:
                self._snack(f"Could not rename: {ex}")
                self._compose_tab_exit_rename_mode()
                return
            try:
                with session_scope() as s:
                    st = version_storage.update_document_path_after_rename(s, old_r, new_r)
                if st == "collision":
                    try:
                        new_path.rename(old)
                    except OSError:
                        pass
                    self._snack("Rename blocked: another document already uses that path in the library.")
                    self._compose_tab_exit_rename_mode()
                    return
            except BaseException:
                try:
                    new_path.rename(old)
                except OSError:
                    pass
                self._snack("Could not update document history after rename.")
                self._compose_tab_exit_rename_mode()
                return
            self.current_path = new_path
            self._compose_tab_exit_rename_mode()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._refresh_compare_tab_candidate_ui()
            self._sync_version_toolbar_state()
            self._refresh_title_bar()
            self._snack(f'Renamed to "{name}".')

    def _on_compose_reading_wrap_size(self, e: ft.LayoutSizeChangeEvent) -> None:
        """Size reading column as a fraction of the real compose width, capped at READING_MAX_PX."""
        avail = max(200.0, float(e.width))
        reading_w = int(min(float(READING_MAX_PX), max(240, avail * COMPOSE_READING_WIDTH_FRAC)))
        cur = int(self._compose_reading_card.width or 0)
        self._last_editor_content_w = max(120.0, float(reading_w - _COMPOSE_MARGIN_COL_W - 8))
        if cur == reading_w:
            return
        self._compose_reading_card.width = reading_w
        if _ctrl_on_page(self._compose_reading_card):
            self._compose_reading_card.update()
        self._margin_gen += 1
        self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)

    def _on_editor_size_change(self, e: ft.LayoutSizeChangeEvent) -> None:
        cw = max(120.0, float(e.width))
        self._last_editor_content_w = cw
        reported = float(e.height)
        paras = split_paragraphs(self.editor.value or "")
        est = estimate_total_editor_height(paras, cw)
        self._last_editor_h = max(reported, est * 0.98)
        self._apply_compose_sparkle_heights()

    def _on_editor_change(self, _e: ft.ControlEvent) -> None:
        self._refresh_title_bar()
        if self._main_tab_index == 0:
            self._margin_gen += 1
            gen = self._margin_gen
            self.page.run_task(self._debounced_compose_rebuild, gen)
        if not self.current_path:
            return
        self._autosave_gen += 1
        agen = self._autosave_gen
        self.page.run_task(self._autosave_after_idle, agen)

    async def _debounced_compose_rebuild(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._margin_gen:
            return
        if self._main_tab_index != 0:
            return
        self._rebuild_compose_sparkle_slots()
        self._apply_compose_sparkle_heights()

    async def _autosave_after_idle(self, gen: int) -> None:
        await asyncio.sleep(AUTOSAVE_IDLE_SEC)
        if gen != self._autosave_gen:
            return
        await self.save_file(silent=True, snapshot_reason="autosave")

    def _on_selection_change(self, e: ft.TextSelectionChangeEvent) -> None:
        t = (e.selected_text or "").strip()
        if t:
            self.last_selection = e.selected_text or ""

    def _chat_model_tooltip(self) -> str:
        return f"Einstellungen — Chat-Modell wählen (aktuell: {self.ollama_model})"

    async def _refresh_ki_chat_model_dropdown(self) -> None:
        try:
            chat_opts, _ = await classify_installed_models(self.ollama)
        except BaseException:
            return
        m = (self.ollama_model or "").strip()
        if m and m not in chat_opts:
            chat_opts = [m, *chat_opts]
        if not chat_opts and m:
            chat_opts = [m]
        self._chat_model_options = chat_opts
        if chat_opts:
            picked = m if m in chat_opts else chat_opts[0]
            if picked != m:
                self.ollama_model = picked
                store_db.settings_set(self._db, store_db.SETTINGS_CHAT, self.ollama_model)
        self._sync_chat_model_ui()

    def _sync_chat_model_ui(self) -> None:
        m = (self.ollama_model or "").strip()
        self._chat_model_btn.tooltip = self._chat_model_tooltip()
        if m and m not in self._chat_model_options:
            self._chat_model_options = [m, *self._chat_model_options]
        if _ctrl_on_page(self._chat_model_btn):
            self._chat_model_btn.update()

    def _refresh_chat_model_button(self) -> None:
        self._sync_chat_model_ui()

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
        self._compare_editor.cursor_color = config.FEDORA_BLUE
        self._compare_editor.selection_color = config.SELECTION_OVERLAY
        self.dirty_dot.color = config.FEDORA_BLUE
        self._sync_side_panel_chrome()
        self.center_panel.bgcolor = config.SURFACE
        self._sticky_tab_header.bgcolor = config.SURFACE
        self._ki_sticky_tab_header.bgcolor = ft.Colors.TRANSPARENT
        self._ki_topic_shell.bgcolor = config.SURFACE
        if self._header_shell:
            self._header_shell.bgcolor = config.SURFACE_VARIANT
        if self._menu_bar:
            self._menu_bar.style = self._menu_bar_style()
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
            self._compare_editor.update()
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

    async def _run_compare_margin_action(self, action_id: str, index: int) -> None:
        if index < 0 or index >= len(self._compare_right_fields):
            return
        override = self._compare_right_fields[index].value or ""
        await self.run_margin_action(action_id, index, text_override=override)

    async def _open_project_page(self, _e: ft.ControlEvent | None = None) -> None:
        await self.page.launch_url(_PROJECT_PAGE_URL)

    def _paragraph_sparkle_menu_control(self, para_index: int, *, for_compare: bool, compact: bool = False) -> ft.Control:
        """Compose: cascade Discuss / Change / Evaluate. Compare cube: Change-topic prompts only (flat popup)."""
        tooltip = (
            "Change prompts for this paragraph"
            if for_compare
            else "LLM prompts for this paragraph"
        )
        task = self._run_compare_margin_action if for_compare else self.run_margin_action
        _spark_icon = 14 if for_compare else (15 if compact else 18)
        _spark_pad: int | ft.Padding = (
            ft.padding.symmetric(horizontal=3, vertical=2)
            if for_compare
            else (2 if compact else 4)
        )
        _text_sz = 12 if compact else 13

        _sparkle_btn_style = ft.ButtonStyle(
            color=ft.Colors.with_opacity(0.45, ft.Colors.WHITE),
            padding=(
                ft.padding.symmetric(horizontal=2, vertical=1)
                if for_compare
                else ft.padding.all(1 if compact else 2)
            ),
            visual_density=ft.VisualDensity.COMPACT,
        )

        if not prompts.MARGIN_ACTIONS:
            return ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=_spark_icon,
                icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                tooltip=tooltip,
                padding=_spark_pad,
                style=_sparkle_btn_style,
                menu_position=ft.PopupMenuPosition.UNDER,
                items=[
                    ft.PopupMenuItem(
                        content=ft.Text("Add prompts in Settings → Prompts", size=13),
                    ),
                ],
            )

        # Compare action card: only Change-topic actions (no Discuss / Evaluate presets).
        if for_compare:
            change_acts = tuple(sorted(prompts.actions_for_topic(TOPIC_CHANGE), key=lambda a: a.label.casefold()))
            if not change_acts:
                return ft.PopupMenuButton(
                    icon=ft.Icons.AUTO_AWESOME,
                    icon_size=_spark_icon,
                    icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                    tooltip=tooltip,
                    padding=_spark_pad,
                    style=_sparkle_btn_style,
                    menu_position=ft.PopupMenuPosition.UNDER,
                    items=[
                        ft.PopupMenuItem(
                            content=ft.Text("No Change prompts. Add them in Settings → Prompts", size=13),
                        ),
                    ],
                )
            compare_items = [
                ft.PopupMenuItem(
                    content=ft.Text(a.label, size=_text_sz),
                    on_click=lambda e, aid=a.id, ix=para_index: self.page.run_task(task, aid, ix),
                )
                for a in change_acts
            ]
            return ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=_spark_icon,
                icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                tooltip=tooltip,
                padding=_spark_pad,
                style=_sparkle_btn_style,
                menu_position=ft.PopupMenuPosition.UNDER,
                items=compare_items,
            )

        rows_for_menu: list[tuple[str, tuple[prompts.MarginAction, ...]]] = []
        for topic in (TOPIC_DISCUSS, TOPIC_CHANGE, TOPIC_EVALUATE):
            acts = prompts.actions_for_topic(topic)
            if not acts:
                continue
            cat_label = _TOPIC_MENU_LABEL.get(topic, topic)
            sorted_acts = tuple(sorted(acts, key=lambda a: a.label.casefold()))
            rows_for_menu.append((cat_label, sorted_acts))

        if not rows_for_menu:
            return ft.PopupMenuButton(
                icon=ft.Icons.AUTO_AWESOME,
                icon_size=_spark_icon,
                icon_color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
                tooltip=tooltip,
                padding=_spark_pad,
                style=_sparkle_btn_style,
                menu_position=ft.PopupMenuPosition.UNDER,
                items=[
                    ft.PopupMenuItem(content=ft.Text("No prompts for any topic.", size=13)),
                ],
            )

        category_controls: list[ft.Control] = []
        for cat_label, sorted_acts in rows_for_menu:
            leaves: list[ft.Control] = []
            for a in sorted_acts:
                item_label = f"{cat_label} → {a.label}"
                leaves.append(
                    ft.MenuItemButton(
                        content=ft.Text(item_label, size=_text_sz),
                        on_click=lambda e, action_id=a.id, ix=para_index: self.page.run_task(
                            task, action_id, ix
                        ),
                    )
                )
            category_controls.append(
                ft.SubmenuButton(
                    content=ft.Text(cat_label, size=_text_sz),
                    controls=leaves,
                )
            )

        root_menu = ft.SubmenuButton(
            content=ft.Icon(
                ft.Icons.AUTO_AWESOME,
                size=_spark_icon,
                color=ft.Colors.with_opacity(0.85, config.FEDORA_BLUE),
            ),
            tooltip=tooltip,
            style=_sparkle_btn_style,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            controls=category_controls,
            menu_style=ft.MenuStyle(
                alignment=ft.Alignment.TOP_RIGHT,
                visual_density=ft.VisualDensity.COMPACT,
            ),
        )

        return ft.MenuBar(
            controls=[root_menu],
            style=ft.MenuStyle(
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                shadow_color={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
                elevation={ft.ControlState.DEFAULT: 0},
                visual_density=ft.VisualDensity.COMPACT,
            ),
        )

    def _compose_sparkle_menu_control(self, para_index: int) -> ft.Control:
        return self._paragraph_sparkle_menu_control(para_index, for_compare=False)

    def _on_compare_row_hover(self, e: ft.ControlEvent, actions_wrap: ft.Container) -> None:
        actions_wrap.opacity = 1.0 if e.data else 0.0
        if _ctrl_on_page(actions_wrap):
            actions_wrap.update()

    def _rebuild_compose_sparkle_slots(self) -> None:
        buf = self.editor.value or ""
        cur = split_paragraphs(buf)
        if not cur:
            cur = [""]
        self._compose_sparkle_column.controls.clear()
        self._compose_sparkle_roots.clear()
        for i in range(len(cur)):
            menu = self._compose_sparkle_menu_control(i)
            slot = ft.Container(
                content=menu,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.Padding.only(top=2, bottom=2),
                clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            )
            self._compose_sparkle_roots.append(slot)
            self._compose_sparkle_column.controls.append(slot)
        if _ctrl_on_page(self._compose_sparkle_column):
            self._compose_sparkle_column.update()

    def _apply_compose_sparkle_heights(self) -> None:
        if not self._compose_sparkle_roots:
            return
        paras = split_paragraphs(self.editor.value or "")
        if not paras:
            paras = [""]
        inner_w = max(120.0, self._last_editor_content_w)
        wts = paragraph_compose_slot_weights(paras, inner_w)
        heights = distribute_heights(wts, self._last_editor_h)
        n = min(len(self._compose_sparkle_roots), len(heights))
        for i in range(n):
            self._compose_sparkle_roots[i].height = heights[i]
        if _ctrl_on_page(self._compose_sparkle_column):
            self._compose_sparkle_column.update()

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

        self._set_ki_topic(_ki_topic_index_for_prompt_topic(act.topic))

        self._append_chat_line("user", f"Paragraph {idx + 1}: {act.label}")

        reply = ft.Text("", size=12, selectable=True, color=ft.Colors.GREY_100)
        footer = ft.Row(spacing=8, visible=False)
        bubble = ft.Column([reply, footer], tight=True, spacing=8)
        wrap = ft.Container(
            content=bubble,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.GREY_800),
            border_radius=10,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        self._chat_history.controls.append(wrap)
        if _ctrl_on_page(self._chat_history):
            self._chat_history.update()

        messages: list[dict[str, str]] = [
            {"role": "system", "content": act.system_prompt},
            {"role": "user", "content": act.user_template.format(text=para)},
        ]
        acc = ""
        try:
            stream = await self.ollama.chat(
                model=self.ollama_model,
                messages=messages,
                stream=True,
            )
            async for part in stream:
                acc += chat_stream_delta(part)
                live = _strip_change_topic_preamble(acc) if act.topic == TOPIC_CHANGE else acc
                reply.value = live.strip() or "…"
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
                reply.value = f"(Error) {ollama_error_message(ex_final)}"
                if _ctrl_on_page(reply):
                    reply.update()
                if _ctrl_on_page(self._chat_history):
                    self._chat_history.update()
                return

        acc = (acc or "").strip()
        if act.topic == TOPIC_CHANGE:
            acc = _strip_change_topic_preamble(acc)
        if not acc:
            reply.value = "(Empty reply from model.)"
            if _ctrl_on_page(reply):
                reply.update()
            footer.controls = [
                ft.TextButton("Dismiss", on_click=lambda _e, f=footer: self._hide_prompt_footer(f)),
            ]
            footer.visible = True
            if _ctrl_on_page(footer):
                footer.update()
            return

        reply.value = acc
        if _ctrl_on_page(reply):
            reply.update()

        if act.topic == TOPIC_CHANGE:
            footer.controls = [
                ft.FilledButton(
                    "Review",
                    tooltip="Open Compare with this text as candidate",
                    on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                        self._stage_ai_candidate_async, i, r, f, aid
                    ),
                ),
                ft.TextButton("Dismiss", on_click=lambda _e, f=footer: self._hide_prompt_footer(f)),
            ]
        else:
            footer.controls = [
                ft.TextButton("Dismiss", on_click=lambda _e, f=footer: self._hide_prompt_footer(f)),
            ]
        footer.visible = True
        if _ctrl_on_page(footer):
            footer.update()

    def _on_tree_search_change(self, _e: ft.ControlEvent | None = None) -> None:
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()

    def _show_rename_path_dialog(self, path: Path, *, is_dir: bool) -> None:
        root = config.DOCUMENTS.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self._snack("Cannot rename outside the documents folder.")
            return

        is_md_file = not is_dir and path.suffix.lower() == ".md"
        name_field = ft.TextField(
            value=path.stem if is_md_file else path.name,
            autofocus=True,
            dense=True,
            width=280 if is_md_file else 360,
        )
        dialog_content: ft.Control
        if is_md_file:
            dialog_content = ft.Row(
                [name_field, ft.Text(".md", size=14, color=ft.Colors.GREY_400)],
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            dialog_content = name_field

        async def apply_async() -> None:
            raw = (name_field.value or "").strip()
            if is_md_file:
                if raw.lower().endswith(".md"):
                    raw = raw[: -len(".md")].strip()
                new_name = f"{raw}.md" if raw else ""
            else:
                new_name = raw
            if not new_name or new_name in (".", ".."):
                self._snack("Invalid name.")
                return
            if "/" in new_name or "\\" in new_name:
                self._snack("Name cannot contain path separators.")
                return

            new_path = (path.parent / new_name).resolve()
            try:
                new_path.relative_to(root)
            except ValueError:
                self._snack("Invalid target path.")
                return

            if new_path == path.resolve():
                self.page.pop_dialog()
                return
            if new_path.exists():
                self._snack("A file or folder with that name already exists.")
                return

            old_resolved = path.resolve()
            if self.current_path and self._is_dirty():
                cur = self.current_path.resolve()
                if not is_dir and cur == old_resolved:
                    await self.save_file(silent=True, snapshot_reason="pre_switch")
                elif is_dir:
                    try:
                        cur.relative_to(old_resolved)
                        await self.save_file(silent=True, snapshot_reason="pre_switch")
                    except ValueError:
                        pass

            try:
                path.rename(new_path)
            except OSError as ex:
                self._snack(f"Rename failed: {ex}")
                return

            new_resolved = new_path.resolve()
            _db_collision = "iterthink_rename_db_collision"
            try:
                with session_scope() as s:
                    if is_dir:
                        st = version_storage.update_document_paths_after_dir_rename(s, old_resolved, new_resolved)
                    else:
                        st = version_storage.update_document_path_after_rename(s, old_resolved, new_resolved)
                    if st == "collision":
                        raise RuntimeError(_db_collision)
            except RuntimeError as ex:
                if ex.args and ex.args[0] == _db_collision:
                    try:
                        new_path.rename(path)
                    except OSError:
                        self._snack("Rename rolled back with a database conflict; check document paths in settings.")
                        return
                    self._snack("That name conflicts with the version library database.")
                    return
                raise
            except Exception:
                try:
                    new_path.rename(path)
                except OSError:
                    pass
                raise

            if self.current_path:
                cur = self.current_path.resolve()
                if not is_dir and cur == old_resolved:
                    self.current_path = new_resolved
                elif is_dir:
                    try:
                        rel = cur.relative_to(old_resolved)
                        self.current_path = new_resolved / rel
                    except ValueError:
                        pass

            self.page.pop_dialog()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._refresh_compare_tab_candidate_ui()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()
            self._refresh_title_bar()
            self._snack("Renamed.")

        def on_ok(_e: ft.ControlEvent | None = None) -> None:
            self.page.run_task(apply_async)

        name_field.on_submit = lambda _e: self.page.run_task(apply_async)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Rename folder" if is_dir else "Rename file", weight=ft.FontWeight.W_600),
                content=dialog_content,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton("OK", on_click=on_ok),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

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

        def render_level(node: dict[str, Any], parent_path: Path, depth: int = 0) -> list[ft.Control]:
            ctrls: list[ft.Control] = []
            for dirname in sorted(k for k in node if k != "_files"):
                sub = node[dirname]
                folder_path = parent_path / dirname
                inner = render_level(sub, folder_path, depth + 1)
                ctrls.append(
                    ft.ExpansionTile(
                        title=ft.GestureDetector(
                            mouse_cursor=ft.MouseCursor.CLICK,
                            on_double_tap=lambda _e, p=folder_path: self._show_rename_path_dialog(p, is_dir=True),
                            content=ft.Text(dirname, size=13, color=ft.Colors.GREY_200),
                        ),
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
                    ft.GestureDetector(
                        mouse_cursor=ft.MouseCursor.CLICK,
                        on_tap=lambda _e, fp=fpath: self.page.run_task(self.open_file, fp),
                        on_double_tap=lambda _e, fp=fpath: self._show_rename_path_dialog(fp, is_dir=False),
                        content=ft.Container(
                            content=ft.Text(fname, size=12, font_family="monospace"),
                            padding=ft.Padding.symmetric(horizontal=12, vertical=2),
                        ),
                    )
                )
            return ctrls

        self.tree_column.controls.extend(render_level(tree, root))

    async def open_file(self, path: Path) -> None:
        if self.current_path and path != self.current_path and self._is_dirty():
            await self.save_file(silent=True, snapshot_reason="pre_switch")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not open: {ex}")
            return
        self._compare_candidate_source = "draft"
        self._compare_snapshot_version_id = None
        self._pending_ai_accept_action_id = None
        self.current_path = path
        self.last_saved_text = text
        self.editor.value = text
        self._compare_editor.value = text
        self._compare_baseline_snapshot = text
        self._main_tabs.selected_index = 0
        self._main_tab_index = 0
        self._refresh_compare_tab_candidate_ui()
        self._sync_version_toolbar_state()
        if _ctrl_on_page(self.editor):
            self.editor.update()
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

    def _next_dated_note_path(self) -> Path:
        root = config.DOCUMENTS
        root.mkdir(parents=True, exist_ok=True)
        stamp = date.today().strftime("%Y%m%d")
        n = 1
        while True:
            cand = root / f"{stamp}-{n}.md"
            if not cand.exists():
                return cand
            n += 1

    async def _startup_open_default_note(self) -> None:
        if not self.current_path:
            await self.new_file(None)

    async def new_file(self, _e: ft.ControlEvent | None = None) -> None:
        config.DOCUMENTS.mkdir(parents=True, exist_ok=True)
        path = self._next_dated_note_path()
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

    async def save_file(
        self,
        _e: ft.ControlEvent | None = None,
        *,
        silent: bool = False,
        snapshot_reason: version_storage.SnapshotReason | None = None,
        version_display_label: str | None = None,
    ) -> None:
        if not self.current_path:
            if not silent:
                self._snack("Open or create a note first.")
            return
        buf = self._working_document_text()
        reason: version_storage.SnapshotReason = snapshot_reason or ("autosave" if silent else "manual")
        try:
            self.current_path.write_text(buf, encoding="utf-8")
        except OSError as ex:
            self._snack(f"Save failed: {ex}")
            return
        self.last_saved_text = buf
        try:
            with session_scope() as s:
                if version_display_label:
                    version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        buf,
                        "ai_apply",
                        display_label=version_display_label,
                    )
                else:
                    version_storage.persist_version_snapshot(s, self.current_path.resolve(), buf, reason)
        except BaseException:
            pass
        self._refresh_compare_tab_candidate_ui()
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()
        self._margin_gen += 1
        if self._main_tab_index == 0:
            self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)
        else:
            self._refresh_compare_diff_immediate()
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
        about_body = ft.Text(
            "Local Markdown writer with Ollama.\n\n"
            "File → Settings: models, paths, appearance, and margin prompts.\n"
            "Compose: paragraph sparkle actions; Compare: Latest (saved) vs other version, picker in header.\n\n"
            "Ctrl+J / ⌘+J: KI-Panel rechts ein/aus.",
            size=14,
        )
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("About Iterthink", weight=ft.FontWeight.W_600),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Image(
                                    src=str(config.APP_SYMBOL_PNG),
                                    width=48,
                                    height=48,
                                    fit=ft.BoxFit.CONTAIN,
                                ),
                                ft.Text("Iterthink", size=20, weight=ft.FontWeight.W_600),
                            ],
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        about_body,
                    ],
                    spacing=16,
                    tight=True,
                    width=420,
                ),
                actions=[ft.TextButton("OK", on_click=lambda e: self.page.pop_dialog())],
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
            overlay_color=ft.Colors.with_opacity(0.07, ft.Colors.WHITE),
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
        bar_btn = self._submenu_bar_button_style()
        drop = self._submenu_dropdown_style()

        def _top_submenu(label: str, items: list[ft.Control]) -> ft.SubmenuButton:
            return ft.SubmenuButton(
                content=ft.Text(label),
                style=bar_btn,
                menu_style=drop,
                alignment_offset=ft.Offset(0, 0),
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
                _top_submenu(
                    "View",
                    [
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
                _top_submenu(
                    "Help",
                    [
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
        compact_rail: bool = False,
    ) -> ft.Control:
        """Hairline + centered pill. Expanded card: narrow strip, pill on hover only.
        Collapsed rail: strip fills width, pill always visible for affordance + easier taps."""
        hair = ft.BorderSide(1, ft.Colors.with_opacity(0.1, ft.Colors.WHITE))
        edge_border = ft.border.only(right=hair) if hairline_after else ft.border.only(left=hair)
        pill_idle = ft.Colors.with_opacity(0.24, ft.Colors.WHITE) if compact_rail else ft.Colors.TRANSPARENT
        pill = ft.Container(
            width=6 if compact_rail else 4,
            height=52 if compact_rail else 40,
            border_radius=3 if compact_rail else 2,
            bgcolor=pill_idle,
        )

        def _on_strip_hover(e: ft.ControlEvent) -> None:
            if e.data:
                pill.bgcolor = config.FEDORA_BLUE
            else:
                pill.bgcolor = pill_idle
            if _ctrl_on_page(pill):
                pill.update()

        strip = ft.Container(
            **(
                {"expand": True}
                if compact_rail
                else {"width": 10, "expand": True}
            ),
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
            expand=compact_rail,
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
                    self._pane_split_handle(
                        tooltip="Show explorer",
                        on_toggle=self.toggle_left,
                        hairline_after=True,
                        compact_rail=True,
                    ),
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
                    padding=0,
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
                    padding=0,
                    on_hover=self._on_header_chrome_hover,
                    content=ft.Row([menu], vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
        return ft.Stack(stack_children, expand=True, clip_behavior=ft.ClipBehavior.NONE)
