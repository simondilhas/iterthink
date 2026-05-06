"""Flet UI: MarkdownStudio with Compose/Compare tabs and KI sidebar."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Literal

import flet as ft
from ollama import AsyncClient

from iterthink import config, document_import, passphrase_keyring, plan_compare_panel
from iterthink import store_db, vault_store, version_storage
from iterthink.db.session import session_scope
from iterthink.studio_asset_compare import MarkdownStudioAssetCompare
from iterthink.studio_checks_ui import MarkdownStudioChecksUi
from iterthink.studio_history import MarkdownStudioCompareText
from iterthink.studio_focus_area import MarkdownStudioCompose
from iterthink.studio_explorer import MarkdownStudioExplorer
from iterthink.studio_ki import MarkdownStudioKi
from iterthink.studio_llm import MarkdownStudioLlm, build_ki_tier_tabs
from iterthink.studio_shell import MarkdownStudioShell
from iterthink.studio_sidebars import MarkdownStudioSidebars
from iterthink.studio_constants import (
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_CANDIDATE_DROPDOWN_OPTION_STYLE,
    COMPARE_EVAL_COL_W,
    COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX,
    COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX,
    COMPOSE_EDITOR_CONTENT_PAD_TOP_PX,
    COMPARE_KEY_CURRENT as _COMPARE_KEY_CURRENT,
    KI_TAB_BAR_TO_PILLS_GAP_PX,
    KI_TAB_BODY_MIN_HEIGHT_PX,
    KI_TAB_ICON_PX,
    KI_TAB_PAGE_PAD_V_PX,
    KI_TIER_TAB_ICON_PX,
    RESULT_CARD_W as _RESULT_CARD_W,
    SIDEBAR_EXPANDED_WIDTH_PX,
    SIDEBAR_INNER_BORDER_RADIUS_PX,
    SIDEBAR_INNER_PAD_PX,
    SIDEBAR_TOOLBAR_ROW_H_PX,
    TAB_HISTORY,
    TAB_PRESENT,
    TAB_FUTURE,
)
from iterthink.studio_util import (
    KI_TIERS,
    ctrl_on_page as _ctrl_on_page,
    normalize_cloud_vendor,
    normalize_ki_tier,
)
# Autosave: disk idle vs snapshot idle (see studio_constants). Compare: left = latest Compose; right = draft / snapshot / AI.
# Layout literals: iterthink.studio_constants

CompareCandidateSource = Literal["draft", "ai_preview", "snapshot", "pdf_original", "docx_original"]


class MarkdownStudio(
    MarkdownStudioShell,
    MarkdownStudioCompose,
    MarkdownStudioCompareText,
    MarkdownStudioSidebars,
    MarkdownStudioKi,
    MarkdownStudioExplorer,
    MarkdownStudioChecksUi,
    MarkdownStudioAssetCompare,
    MarkdownStudioLlm,
):
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
        self._api_secrets_cache: dict[str, str] | None = None
        if vault_store.vault_exists():
            kr = passphrase_keyring.get_stored_passphrase()
            if kr:
                self.try_unlock_credential_vault(kr)
        self.ki_tier: str = normalize_ki_tier(store_db.settings_get(self._db, store_db.SETTINGS_KI_TIER))
        self.cloud_vendor: str = normalize_cloud_vendor(store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_VENDOR))
        self.company_openai_model: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_COMPANY_OPENAI_MODEL) or "gpt-4o-mini"
        )
        self.company_openai_base_url: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_COMPANY_OPENAI_BASE_URL) or "https://api.openai.com/v1"
        )
        self.cloud_anthropic_model: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_ANTHROPIC_MODEL) or "claude-3-5-sonnet-20241022"
        )
        self.cloud_openai_model: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_OPENAI_MODEL) or "gpt-4o-mini"
        )
        self.cloud_google_model: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_GOOGLE_MODEL) or "gemini-1.5-flash"
        )
        self.current_path: Path | None = None
        self.last_saved_text: str = ""
        self.last_selection: str = ""
        # Compose sparkle: snapshot (text, start) before PopupMenuButton steals focus/clears selection.
        self._compose_margin_menu_snap: tuple[str, int] | None = None
        # Last non-collapsed compose selection (code-unit offsets); survives blur until caret leaves range or buffer edits.
        self._compose_sel_span: tuple[int, int] | None = None
        # Right-click context menu wrapping the editor; items rebuilt when prompts.yaml reloads.
        self._editor_ctx_menu: ft.ContextMenu | None = None
        self.left_open: bool = True

        self._margin_gen: int = 0
        self._compare_diff_gen: int = 0
        self._main_tab_index: int = TAB_PRESENT
        self._compare_tab_bar_hover_index1: bool = False
        self._compare_dropdown_hover: bool = False
        self._compare_candidate_source: CompareCandidateSource = "draft"
        self._compare_snapshot_version_id: int | None = None
        self._pending_ai_accept_action_id: str | None = None
        # History tab: right column = current draft (editable)
        self._compare_right_fields: list[ft.TextField] = []
        self._compare_left_diff_texts: list[ft.Text] = []
        self._compare_row_pill_hosts: list[ft.Container] = []
        self._compare_row_stable_texts: list[str] = []
        self._compare_pill_gen: int = 0
        self._compare_refine_gen: int = 0
        # Future tab: left column = current draft (editable), right column = AI proposal (read-only)
        self._future_left_fields: list[ft.TextField] = []
        self._future_right_diff_texts: list[ft.Text] = []
        self._future_row_pill_hosts: list[ft.Container] = []
        self._future_row_stable_texts: list[str] = []
        # Compose text frozen when opening Compare (draft); left column diffs vs this, not live editor drift.
        self._compare_baseline_snapshot: str = ""
        self._compose_tab_inline_rename_active: bool = False
        self._compose_tab_rename_lock = asyncio.Lock()

        self._compare_pdf_peer_snapshot_id: int | None = None
        self._compare_pdf_scroll_guard: bool = False
        self._compare_pdf_left_max_scroll: float = 1.0
        self._compare_pdf_right_max_scroll: float = 1.0
        self._plan_overlay_mode: bool = False
        self._plan_overlay_gen: int = 0
        self._fp_import = ft.FilePicker()
        self._import_kind: str | None = None
        self._import_flow: str | None = None
        self._import_target_md: Path | None = None

        self._header_hide_gen: int = 0
        self._header_shell: ft.Container | None = None
        self._license_banner_host: ft.Container | None = None
        self._header_menu_open: int = 0
        self._header_chrome_hover: bool = False

        self.editor = ft.TextField(
            multiline=True,
            max_lines=None,
            min_lines=1,
            border=ft.InputBorder.NONE,
            filled=False,
            hint_text="Write…",
            content_padding=ft.padding.only(
                left=COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX,
                right=COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX,
                top=COMPOSE_EDITOR_CONTENT_PAD_TOP_PX,
                bottom=0,
            ),
            text_style=ft.TextStyle(
                font_family="monospace",
                size=COMPARE_COL_FONT_SIZE,
                height=COMPARE_COL_LINE_HEIGHT,
                color=ft.Colors.GREY_100,
            ),
            cursor_color=config.FEDORA_BLUE,
            selection_color=config.SELECTION_OVERLAY,
            enable_interactive_selection=True,
            on_change=self._on_editor_change,
            on_selection_change=self._on_selection_change,
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

        # items=… is what programmatic ContextMenu.open() shows. secondary_trigger is
        # longPress so the built-in Listener does not handle right-button down (that
        # still reaches the TextField on desktop; web: browser menu disabled in app_entry).
        self._editor_ctx_menu = ft.ContextMenu(
            content=self.editor,
            items=self._build_editor_ctx_menu_items(),
            secondary_trigger=ft.ContextMenuTrigger.LONG_PRESS,
            tertiary_trigger=ft.ContextMenuTrigger.LONG_PRESS,
        )
        self._editor_shell = ft.Container(
            expand=True,
            content=ft.Row(
                [ft.Container(content=self._editor_ctx_menu, expand=True)],
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

        self._compose_plan_host = ft.Container(expand=True, visible=False)
        self._compose_editor_area_gesture = ft.GestureDetector(
            content=self._editor_shell,
            on_secondary_tap_down=self._on_compose_editor_area_secondary_down,
            expand=True,
        )
        self._compose_editor_and_actions_stack = ft.Container(
            expand=True,
            content=self._compose_editor_area_gesture,
        )
        self._compose_editor_shell_wrapped = ft.Container(
            content=self._compose_editor_and_actions_stack,
            expand=True,
        )
        self._compose_reading_inner = ft.Column(
            [
                self._compose_plan_host,
                self._compose_editor_shell_wrapped,
            ],
            expand=True,
            spacing=8,
        )
        self._compose_reading_card = ft.Container(
            width=400,
            content=self._compose_reading_inner,
        )
        self._compose_reading_wrap = ft.Container(
            expand=True,
            alignment=ft.Alignment.TOP_CENTER,
            content=self._compose_reading_card,
        )
        self._compose_centered_row = ft.Row(
            [self._compose_reading_wrap],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
            on_size_change=self._on_compose_reading_wrap_size,
        )
        self._compose_tab_body_stack = ft.Container(
            expand=True,
            content=ft.Column(
                [self._compose_centered_row],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        )
        self._compose_tab_body = ft.Container(
            expand=True,
            padding=ft.padding.only(top=4, bottom=12),
            content=self._compose_tab_body_stack,
        )

        _dd_menu_style = ft.MenuStyle(
            bgcolor=config.SURFACE,
            elevation=12,
            shadow_color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
            visual_density=ft.VisualDensity.COMPACT,
        )
        # Dense toolbar dropdown: line height 1.0 keeps label vertically aligned with the
        # trailing chevron; the tab strip below must be tall enough for Material input (~36px+).
        _tb_dd_text_style = ft.TextStyle(size=12, height=1.0, color=ft.Colors.GREY_200)
        self._compare_candidate_dropdown = ft.Dropdown(
            expand=True,
            dense=True,
            text_style=_tb_dd_text_style,
            filled=True,
            fill_color=config.SURFACE,
            border=ft.InputBorder.NONE,
            border_width=0,
            content_padding=ft.padding.symmetric(horizontal=4, vertical=0),
            menu_style=_dd_menu_style,
            options=[
                ft.dropdown.Option(
                    key=_COMPARE_KEY_CURRENT,
                    text="Current",
                    style=COMPARE_CANDIDATE_DROPDOWN_OPTION_STYLE,
                )
            ],
            value=_COMPARE_KEY_CURRENT,
            disabled=True,
            tooltip="Pick a version snapshot to compare against the current draft.",
            on_select=lambda e: self.page.run_task(self._on_compare_candidate_change_async, e),
        )
        self._compare_dropdown_hover_wrap = ft.Container(
            content=self._compare_candidate_dropdown,
            on_hover=self._on_compare_dropdown_container_hover,
            expand=True,
        )
        self._review_candidate_dropdown = ft.Dropdown(
            expand=True,
            dense=True,
            text_style=_tb_dd_text_style,
            filled=True,
            fill_color=config.SURFACE,
            border=ft.InputBorder.NONE,
            border_width=0,
            content_padding=ft.padding.symmetric(horizontal=4, vertical=0),
            menu_style=_dd_menu_style,
            options=[],
            disabled=True,
            tooltip="Pick an AI proposal or import to review against the current draft.",
            on_select=lambda e: self.page.run_task(self._on_compare_candidate_change_async, e),
        )
        _compare_bulk_icon_style = ft.ButtonStyle(
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
            visual_density=ft.VisualDensity.COMPACT,
        )
        self._compare_approve_all_btn = ft.IconButton(
            ft.Icons.DONE_ALL,
            icon_size=18,
            icon_color=config.FEDORA_BLUE,
            tooltip="Apply all paragraphs to the document",
            style=_compare_bulk_icon_style,
            visible=False,
            on_click=lambda _e: self.page.run_task(self._compare_approve_all_async),
        )
        self._compare_decline_all_btn = ft.IconButton(
            ft.Icons.CLOSE_ROUNDED,
            icon_size=18,
            icon_color=ft.Colors.GREY_400,
            tooltip="Reset all paragraphs to match latest (left)",
            style=_compare_bulk_icon_style,
            visible=False,
            on_click=lambda _e: self.page.run_task(self._compare_decline_all_async),
        )
        # History tab label: simple text (dropdown lives in the tab body toolbar)
        self._compare_tab_label_host = ft.Container(
            content=ft.Text("History", size=14, color=ft.Colors.GREY_400),
            expand=True,
            alignment=ft.Alignment.CENTER,
        )
        self._compare_rows_listview = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
        )
        self._compare_paragraph_layer = ft.Container(content=self._compare_rows_listview, expand=True)
        self._compare_pdf_left_lv = ft.ListView(
            expand=True,
            spacing=8,
            padding=ft.padding.all(8),
            on_scroll=self._on_compare_pdf_scroll_left,
        )
        self._compare_pdf_right_lv = ft.ListView(
            expand=True,
            spacing=6,
            padding=ft.padding.all(8),
            on_scroll=self._on_compare_pdf_scroll_right,
        )
        self._plan_compare = plan_compare_panel.build_plan_compare_panel(
            on_baseline=lambda e: self.page.run_task(self._on_plan_pdf_baseline_async, e),
            on_candidate=lambda e: self.page.run_task(self._on_plan_pdf_candidate_async, e),
            on_overlay=self._on_plan_overlay_changed,
        )
        self._plan_compare.overlay_list.visible = False
        self._plan_compare.overlay_list.on_scroll = self._on_compare_pdf_scroll_right
        self._compare_pdf_right_column = ft.Column(
            [self._compare_pdf_right_lv, self._plan_compare.overlay_list],
            expand=True,
            spacing=0,
        )
        self._compare_pdf_split_row = ft.Row(
            [
                ft.Container(
                    content=self._compare_pdf_left_lv,
                    expand=True,
                    border=ft.border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
                    border_radius=8,
                ),
                ft.Container(
                    content=self._compare_pdf_right_column,
                    expand=True,
                    border=ft.border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
                    border_radius=8,
                ),
            ],
            expand=True,
            spacing=8,
        )
        self._compare_pdf_layer = ft.Container(content=self._compare_pdf_split_row, expand=True, visible=False)
        self._compare_editor_holder = ft.Container(content=self._compare_editor, visible=False, height=0)
        # Floating card shown on hover over an Analyse symbol in a History row's eval cell.
        self._result_card_overlay = ft.Container(
            visible=False,
            width=_RESULT_CARD_W,
            bgcolor=ft.Colors.with_opacity(0.96, "#1A1D22"),
            border=ft.border.all(1, ft.Colors.with_opacity(0.55, ft.Colors.GREY_700)),
            border_radius=10,
            padding=ft.padding.all(12),
            shadow=ft.BoxShadow(
                blur_radius=18,
                spread_radius=0,
                color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
                offset=ft.Offset(0, 6),
            ),
            top=0,
            left=4,
            on_hover=self._on_result_card_hover,
            content=ft.Column([], tight=True, spacing=6),
        )
        self._compare_body_stack = ft.Stack(
            controls=[
                self._compare_paragraph_layer,
                self._compare_pdf_layer,
                self._result_card_overlay,
            ],
            expand=True,
        )
        # History tab body
        self._compare_tab_body = ft.Column(
            [
                self._plan_compare.host,
                ft.Row(
                    [self._compare_body_stack],
                    expand=True,
                ),
                self._compare_editor_holder,
            ],
            expand=True,
            spacing=0,
        )
        # Future tab: separate listview and body
        self._future_rows_listview = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
        )
        self._future_paragraph_layer = ft.Container(content=self._future_rows_listview, expand=True)
        self._future_body_stack = ft.Stack(
            controls=[self._future_paragraph_layer],
            expand=True,
        )
        self._future_tab_body = ft.Column(
            [
                ft.Row(
                    [self._future_body_stack],
                    expand=True,
                ),
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
        # ── Tab-specific toolbar (below tab bar) ─────────────────────────────
        _tb_text_style = ft.TextStyle(size=12, color=ft.Colors.GREY_500)
        _tb_icon_color = ft.Colors.GREY_600
        _tb_icon_size = 14
        _tb_pad = ft.padding.symmetric(horizontal=12, vertical=4)

        _tb_label_style = ft.TextStyle(size=12, color=ft.Colors.GREY_600)
        _tb_col_label = lambda t: ft.Text(t, style=_tb_label_style)  # noqa: E731

        # History: left = "Old Version:" + snapshot dropdown  |  right = "Draft"
        self._toolbar_history = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text("Old Version:", style=_tb_label_style),
                                self._compare_dropdown_hover_wrap,
                            ],
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            expand=True,
                        ),
                        expand=1,
                    ),
                    ft.Container(
                        content=ft.Text("Draft", style=_tb_label_style),
                        expand=1,
                        alignment=ft.Alignment(0, 0),
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
            padding=_tb_pad,
            expand=True,
            visible=False,
        )

        # Focus Area: filename + inline rename, horizontally centered in the toolbar strip
        self._toolbar_focus_area = ft.Container(
            content=ft.Row(
                [
                    ft.Container(expand=True),
                    self._compose_tab_filename_row,
                    ft.Container(expand=True),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
            padding=_tb_pad,
            expand=True,
            visible=True,
        )

        # Review: left = "Draft"  |  right = proposal/import selector + bulk accept/decline
        self._toolbar_review = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Text("Draft", style=_tb_label_style),
                        expand=1,
                        alignment=ft.Alignment(0, 0),
                    ),
                    ft.Container(
                        content=ft.Row(
                            [
                                self._review_candidate_dropdown,
                                self._compare_approve_all_btn,
                                self._compare_decline_all_btn,
                            ],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            expand=True,
                        ),
                        expand=1,
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
            padding=_tb_pad,
            expand=True,
            visible=False,
        )

        self._tab_toolbar = ft.Container(
            bgcolor=config.SURFACE,
            content=ft.Column(
                [
                    ft.Stack(
                        [self._toolbar_history, self._toolbar_focus_area, self._toolbar_review],
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        height=44,
                    ),
                    ft.Divider(
                        height=1,
                        thickness=1,
                        color=ft.Colors.with_opacity(0.12, ft.Colors.GREY_600),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        )
        # ─────────────────────────────────────────────────────────────────────

        self._compose_tab_label_row = ft.Row(
            [ft.Text("Focus Area", size=14, color=ft.Colors.GREY_400)],
            tight=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # Future tab label: "Future" + bulk approve/decline buttons
        _future_tab_label_row = ft.Row(
            [ft.Text("Review", size=14, color=ft.Colors.GREY_400)],
            tight=True,
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._future_tab_label_host = ft.Container(
            content=_future_tab_label_row,
            expand=True,
            alignment=ft.Alignment.CENTER,
        )
        self._main_tab_bar = ft.TabBar(
            tabs=[
                ft.Tab(label=self._compare_tab_label_host),
                ft.Tab(label=self._compose_tab_label_row),
                ft.Tab(label=self._future_tab_label_host),
            ],
            scrollable=False,
            tab_alignment=ft.TabAlignment.FILL,
            indicator_size=ft.TabBarIndicatorSize.TAB,
            label_padding=ft.padding.only(bottom=8),
            indicator_color=config.FEDORA_BLUE,
            divider_color=ft.Colors.with_opacity(0.2, ft.Colors.GREY_700),
            on_hover=self._on_main_tab_bar_hover,
        )
        self._tab_bar_view = ft.TabBarView(
            controls=[self._compare_tab_body, self._compose_tab_body, self._future_tab_body],
            expand=True,
        )
        self._sticky_tab_header = ft.Container(
            bgcolor=config.SURFACE,
            padding=ft.padding.only(bottom=2),
            content=self._main_tab_bar,
        )
        self._tabs_inner_column = ft.Column(
            [self._sticky_tab_header, self._tab_toolbar, self._tab_bar_view],
            expand=True,
            spacing=0,
        )
        self._main_tabs = ft.Tabs(
            content=self._tabs_inner_column,
            length=3,
            expand=True,
            selected_index=TAB_PRESENT,
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
        self._disk_autosave_gen: int = 0
        self._snapshot_autosave_gen: int = 0

        self.tree_column = ft.Column(spacing=0, tight=True, scroll=ft.ScrollMode.AUTO, expand=True)
        self._tree_sort_mode = "name_az"
        self._tree_explorer_overflow_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_size=KI_TAB_ICON_PX,
            icon_color=ft.Colors.GREY_400,
            tooltip="Sort tree",
            style=ft.ButtonStyle(padding=ft.padding.all(2)),
            height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            width=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            menu_position=ft.PopupMenuPosition.UNDER,
            items=[
                ft.PopupMenuItem(
                    content=ft.Text("Date (newest first)", size=13),
                    on_click=lambda _e: self._on_tree_sort_selected("mtime_newest"),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Date (oldest first)", size=13),
                    on_click=lambda _e: self._on_tree_sort_selected("mtime_oldest"),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Name A–Z", size=13),
                    on_click=lambda _e: self._on_tree_sort_selected("name_az"),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Name Z–A", size=13),
                    on_click=lambda _e: self._on_tree_sort_selected("name_za"),
                ),
            ],
        )

        self.tree_search_field = ft.TextField(
            hint_text="Search files…",
            dense=True,
            filled=False,
            bgcolor=ft.Colors.TRANSPARENT,
            border=ft.InputBorder.NONE,
            text_size=12,
            cursor_color=config.FEDORA_BLUE,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=0),
            expand=True,
            on_change=self._on_tree_search_change,
        )
        _tree_search_bar = ft.Container(
            expand=True,
            height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            bgcolor=config.SURFACE,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
            border=ft.Border.all(1, ft.Colors.GREY_700),
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=self.tree_search_field,
        )
        self._tree_search_bar = _tree_search_bar

        def _tree_search_rim(focused: bool) -> None:
            _tree_search_bar.border = ft.Border.all(
                1, config.FEDORA_BLUE if focused else ft.Colors.GREY_700
            )
            if _ctrl_on_page(_tree_search_bar):
                _tree_search_bar.update()

        self.tree_search_field.on_focus = lambda _e: _tree_search_rim(True)
        self.tree_search_field.on_blur = lambda _e: _tree_search_rim(False)

        self._tree_add_menu = ft.PopupMenuButton(
            icon=ft.Icons.ADD,
            icon_size=KI_TAB_ICON_PX,
            icon_color=config.FEDORA_BLUE,
            tooltip="New, import…",
            style=ft.ButtonStyle(padding=ft.padding.all(2)),
            height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            width=float(SIDEBAR_TOOLBAR_ROW_H_PX),
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
                ft.PopupMenuItem(
                    content=ft.Text("Import…", size=13),
                    on_click=lambda _e: self.page.run_task(self._tree_import_new_clicked),
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

        # ----- Per-paragraph Analyse checks (KI Analyse tab) -----
        # Active check id whose symbols populate the Compare row eval cells.
        self._active_check_id: str | None = None
        # Per-check results aligned with current candidate paragraph indices.
        self._check_results: dict[str, list[dict | None]] = {}
        # Per-check running flag; True while a paragraph-by-paragraph run is in progress.
        self._check_running: dict[str, bool] = {}
        # Monotonic generation per check; cancels stale background runs.
        self._check_run_gen: dict[str, int] = {}
        # Current candidate-paragraph hashes (used to invalidate results on edit).
        self._check_para_hashes: list[str] = []
        # Eval-cell host containers, parallel to _compare_right_fields, for O(1) refresh.
        self._compare_eval_hosts: list[ft.Container] = []
        # Floating result-card overlay state.
        self._result_card_visible_for: tuple[str, int] | None = None
        self._result_card_hide_gen: int = 0

        self._pill_row_discuss = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._pill_row_change = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._pill_row_analyse = ft.Row(spacing=4, wrap=True, run_spacing=4)
        # Analyse buttons keyed by check_id; updated by _refresh_analyse_button_state.
        self._analyse_buttons: dict[str, ft.FilledButton] = {}
        self._analyse_button_progress: dict[str, ft.ProgressRing] = {}
        self._analyse_button_count: dict[str, ft.Text] = {}
        self._ki_tab_body_heights: list[float] = [
            float(KI_TAB_BODY_MIN_HEIGHT_PX),
            float(KI_TAB_BODY_MIN_HEIGHT_PX),
            float(KI_TAB_BODY_MIN_HEIGHT_PX),
        ]

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
                ft.Container(
                    padding=ft.padding.symmetric(
                        horizontal=4,
                        vertical=KI_TAB_PAGE_PAD_V_PX,
                    ),
                    content=self._pill_row_analyse,
                ),
            ],
            height=float(KI_TAB_BODY_MIN_HEIGHT_PX + 2 * KI_TAB_PAGE_PAD_V_PX),
        )
        _ki_mode_btn_style = ft.ButtonStyle(
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
            padding=ft.padding.all(2),
        )
        _ki_tab_under = 1.5
        self._ki_topic_mode_buttons: list[ft.IconButton] = []
        self._ki_topic_mode_cells: list[ft.Container] = []
        for i, (ic, tip) in enumerate(
            [
                (ft.Icons.CHAT_BUBBLE, "Discuss"),
                (ft.Icons.MODE_EDIT, "Change"),
                (ft.Icons.INSIGHTS, "Analyse"),
            ]
        ):
            sel = i == 0
            ib = ft.IconButton(
                icon=ic,
                tooltip=tip,
                icon_size=KI_TAB_ICON_PX,
                icon_color=config.FEDORA_BLUE if sel else ft.Colors.GREY_400,
                style=_ki_mode_btn_style,
                on_click=lambda e, ix=i: self._set_ki_topic(ix),
            )
            self._ki_topic_mode_buttons.append(ib)
            cell = ft.Container(
                expand=True,
                height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
                alignment=ft.Alignment.CENTER,
                border=ft.border.only(
                    bottom=ft.BorderSide(
                        _ki_tab_under if sel else 0.0,
                        config.FEDORA_BLUE if sel else ft.Colors.TRANSPARENT,
                    )
                ),
                content=ib,
            )
            self._ki_topic_mode_cells.append(cell)
        self._ki_topic_top_strip = ft.Row(
            self._ki_topic_mode_cells,
            expand=True,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._ki_topic_top_bar = ft.Container(
            height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            bgcolor=config.SIDEBAR_SURFACE,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
            alignment=ft.Alignment.CENTER,
            padding=ft.padding.symmetric(horizontal=4, vertical=0),
            content=self._ki_topic_top_strip,
        )
        self._ki_topic_tabs = ft.Tabs(
            content=ft.Container(
                padding=ft.padding.only(top=float(KI_TAB_BAR_TO_PILLS_GAP_PX)),
                content=self._ki_tab_bar_view,
            ),
            length=3,
            selected_index=0,
            on_change=self._on_ki_tabs_change,
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
        _tier_ix = KI_TIERS.index(normalize_ki_tier(self.ki_tier))
        self._ki_tier_tabs = build_ki_tier_tabs(
            selected_index=_tier_ix,
            on_change=self._on_ki_tier_tabs_change,
            icon_size=KI_TIER_TAB_ICON_PX,
            tab_bar_height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
        )
        self._chat_model_options: list[str] = []
        self._chat_composer = ft.Container(
            padding=ft.padding.symmetric(horizontal=0, vertical=6),
            bgcolor=ft.Colors.TRANSPARENT,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    self._ki_tier_tabs,
                    ft.Row(
                        [self._chat_input, self._chat_send_btn],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=4,
                    ),
                ],
                tight=True,
                spacing=6,
            ),
        )
        self._right_chat_section = ft.Container(
            expand=True,
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
        self._ki_sidebar_well = ft.Container(
            expand=True,
            bgcolor=config.SURFACE,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
            padding=float(SIDEBAR_INNER_PAD_PX),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    self._ki_topic_tabs,
                    self._right_chat_section,
                ],
                expand=True,
                spacing=4,
            ),
        )
        self._right_ki_column = self._ki_sidebar_well

        self._pill_row_discuss.on_size_change = self._on_ki_pill_row_size_discuss
        self._pill_row_change.on_size_change = self._on_ki_pill_row_size_change
        self._pill_row_analyse.on_size_change = self._on_ki_pill_row_size_analyse

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
        self._refresh_compare_bulk_buttons()



    def _snack(self, msg: str) -> None:
        self.page.snack_bar = ft.SnackBar(ft.Text(msg))
        self.page.snack_bar.open = True
        self.page.update()

    def ensure_file_pickers(self) -> None:
        # Flet 0.80+: FilePicker is a Service; overlay causes "Unknown control: FilePicker".
        if self._fp_documents not in self.page.services:
            self.page.services.append(self._fp_documents)
            self.page.services.append(self._fp_store)
            self.page.services.append(self._fp_import)
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
        self._apply_compare_candidate_dropdown_tab_chrome()
        self._ki_topic_top_bar.bgcolor = config.SIDEBAR_SURFACE
        self._ki_sidebar_well.bgcolor = config.SURFACE
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
        persist_snapshot: bool = True,
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
        if persist_snapshot:
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
        if self._main_tab_index == TAB_PRESENT:
            self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)
        else:
            self._refresh_compare_diff_immediate()
        self._refresh_title_bar()
        if not silent:
            self._snack("Saved.")

