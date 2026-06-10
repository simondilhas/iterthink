"""MarkdownStudio Flet app: Compose/Compare tabs and KI sidebar."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path
from typing import Literal

import flet as ft
from ollama import AsyncClient

from iterthink import config
from iterthink.ai import passphrase_keyring
from iterthink.persistence import (
    impact_annotations,
    paragraph_user_comments,
    spell_resources,
    store_db,
    vault_store,
    content_repo,
)
from iterthink.services import markdown_docx_export

from . import ui_theme

from . import plan_compare_panel
from iterthink.db.session import reset_engine_cache, session_scope
from .formats.ifc import MarkdownStudioIfcFormat
from .formats.pdf_docx import MarkdownStudioAssetCompare

from .checks_ui import MarkdownStudioChecksUi
from .impact_ui import MarkdownStudioImpactMixin
from .constants import (
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_EVAL_COL_W,
    COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX,
    COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX,
    COMPOSE_EDITOR_CONTENT_PAD_TOP_PX,
    COMPARE_KEY_CURRENT as _COMPARE_KEY_CURRENT,
    HISTORY_COMPARE_DROPDOWN_COLUMNS_GAP_PX,
    KI_TAB_BAR_TO_PILLS_GAP_PX,
    KI_TAB_ICON_PX,
    KI_TAB_PAGE_PAD_V_PX,
    KI_TOPIC_COMMENTS,
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
from .explorer import MarkdownStudioExplorer, first_markdown_in_tree
from .search_results_ui import MarkdownStudioSearchResults
from .focus_area import MarkdownStudioCompose
from .history import (
    CompareCandidateSource,
    MarkdownStudioCompareText,
    build_history_snapshot_dropdown_options,
    history_compare_snapshots,
)
from .ki_comments import paragraph_comment_label, plan_comment_list_label, sorted_comment_rows
from .ki_sidebar import KI_TOPIC_STRIP_DISCUSS_ICON, MarkdownStudioKiSidebar
from .llm_backend import MarkdownStudioLlmBackend, build_llm_tier_tabs, sync_privacy_shield_icon
from .token_cost_ui import build_token_cost_label
from .llm_generation_control import MarkdownStudioLlmGenerationControl
from .main_workspace_tabs import MainWorkspaceTabsMixin
from .shell import MarkdownStudioShell
from .content_tree import MarkdownStudioContentTree
from .sidebars import MarkdownStudioSidebars
from .components import action_rail_icon_button_style
from .util import (
    KI_TIERS,
    ctrl_on_page as _ctrl_on_page,
    normalize_cloud_vendor,
    normalize_ki_tier,
    normalize_save_file_path,
)
# Autosave: disk idle vs snapshot idle (see constants). Compare: left = latest Compose; right = draft / snapshot / AI.
# Layout literals: iterthink.studio.constants

# GTK/Linux: native save dialog after closing a Flet modal needs a short yield.
_EXPORT_SAVE_DIALOG_DELAY_SEC = 0.12


class MarkdownStudio(
    MarkdownStudioShell,
    MarkdownStudioCompose,
    MarkdownStudioCompareText,
    MainWorkspaceTabsMixin,
    MarkdownStudioSidebars,
    MarkdownStudioContentTree,
    MarkdownStudioKiSidebar,
    MarkdownStudioSearchResults,
    MarkdownStudioExplorer,
    MarkdownStudioImpactMixin,
    MarkdownStudioChecksUi,
    MarkdownStudioAssetCompare,   # PDF / DOCX compare rendering
    MarkdownStudioIfcFormat,      # IFC compare rendering (placeholder)
    MarkdownStudioLlmBackend,
    MarkdownStudioLlmGenerationControl,
):
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        # Re-read bootstrap YAML so Review layout matches disk (import-time refresh can be stale).
        config.refresh()
        reset_engine_cache()
        self._store_dir_resolved = config.STORE_DIR.resolve()
        self._fp_documents = ft.FilePicker()
        self._fp_store = ft.FilePicker()
        self._menu_bar: ft.MenuBar | None = None
        self.ollama = AsyncClient(host=config.OLLAMA_HOST) if config.OLLAMA_HOST else AsyncClient()
        self._db = store_db.connect()
        store_db.init_schema(self._db)
        spell_resources.ensure_spell_dictionaries()
        self.ollama_model: str = store_db.settings_get(self._db, store_db.SETTINGS_CHAT) or config.DEFAULT_OLLAMA_MODEL
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
        self.yourcompanyos_api_base_url: str = (
            store_db.settings_get(self._db, store_db.SETTINGS_YOURCOMPANYOS_API_BASE_URL) or ""
        ).strip()
        self.cloud_anthropic_model: str = (
            (store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_ANTHROPIC_MODEL) or "").strip()
        )
        self.cloud_openai_model: str = (
            (store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_OPENAI_MODEL) or "").strip()
        )
        self.cloud_google_model: str = (
            (store_db.settings_get(self._db, store_db.SETTINGS_CLOUD_GOOGLE_MODEL) or "").strip()
        )
        self.current_path: Path | None = None
        self.last_saved_text: str = ""
        self.last_selection: str = ""
        # Compose sparkle: snapshot (text, start) before PopupMenuButton steals focus/clears selection.
        self._compose_margin_menu_snap: tuple[str, int] | None = None
        # Last non-collapsed compose selection (code-unit offsets); survives blur until caret leaves range or buffer edits.
        self._compose_sel_span: tuple[int, int] | None = None
        # List continuation on Enter: previous buffer + re-entrancy guard while rewriting value.
        self._editor_prev_for_list_continue: str = ""
        self._editor_list_continue_applying: bool = False
        self._editor_pending_caret: int | None = None
        self._compose_toolbar_applying: bool = False
        self._compose_toolbar_snap_range: tuple[int, int] | None = None
        self._compose_editor_focused: bool = False
        self._compose_editor_stack_width: float = 400.0
        self._compose_editor_stack_height: float = 320.0
        # Right-click context menu wrapping the editor; items rebuilt when prompts.yaml reloads.
        self._editor_ctx_menu: ft.ContextMenu | None = None
        self.left_open: bool = True

        self._margin_gen: int = 0
        self._compare_diff_gen: int = 0
        self._spell_suggest_gen: int = 0
        self._spell_suggest_cached_body: str = ""
        self._spell_suggest_cached_src_sha: str | None = None
        self._init_main_workspace_tab_fields()
        self._file_drift_dialog_open: bool = False
        self._compare_dropdown_hover: bool = False
        self._compare_version_dd_focused: bool = False
        self._compare_candidate_source: CompareCandidateSource = CompareCandidateSource.DRAFT
        self._compare_snapshot_version_id: int | None = None
        # History tab: newer side (right column) — None means live current draft in ``editor``.
        self._compare_newer_version_id: int | None = None
        self._compare_newer_cached_body: str = ""
        self._compare_newer_dropdown_hover: bool = False
        # Review tab: baseline (left column) — None means live current draft in ``editor``.
        self._review_baseline_version_id: int | None = None
        self._review_baseline_cached_body: str = ""
        # Optional: set before switching to History so tab sync picks a specific snapshot (legacy paths).
        self._pending_post_import_history_vid: int | None = None
        self._pending_ai_accept_action_id: str | None = None
        # AI proposal book-keeping: every change-topic reply persists an ai_proposal snapshot;
        # action_id is kept in-memory so accept can label the apply correctly. _latest_ai_proposal_vid
        # is the most recently persisted proposal for the current document (auto-selected on Review).
        self._ai_proposal_action_ids: dict[int, str] = {}
        self._latest_ai_proposal_vid: int | None = None
        # Sha of the proposal body currently displayed in the Review right column; used to
        # decide whether to persist a new ai_proposal snapshot when the user leaves it.
        self._loaded_proposal_sha: str | None = None
        # History tab: left + right are read-only diff Texts; _compare_right_fields holds hidden carriers
        # so length-based code (hash invalidation, bulk-apply checks) keeps working unchanged.
        self._compare_right_fields: list[ft.TextField] = []
        self._compare_left_diff_texts: list[ft.Text] = []
        self._compare_right_diff_texts: list[ft.Text] = []
        self._compare_row_pill_hosts: list[ft.Container] = []
        self._compare_row_stable_texts: list[str] = []
        # Future-only parallel state: per UI row kind (equal/replace/delete/insert),
        # mapping back to the candidate-paragraph index (None for delete rows), and
        # the original "current draft" paragraph used by decline to restore content.
        self._future_row_kinds: list[str] = []
        self._future_row_cand_idx: list[int | None] = []
        self._future_row_stable_texts: list[str] = []
        self._future_row_old_index: list[int] = []
        self._future_row_insert_after_old: list[int] = []
        # Eval column index → candidate paragraph index (Review rows can interleave deletes/ghosts).
        self._future_eval_cand_indices: list[int] = []
        self._compare_pill_gen: int = 0
        self._compare_refine_gen: int = 0
        # Future tab: left column = current draft (read-only diff with deletions),
        # right column = AI proposal (editable, plain). _compare_right_fields holds the editable
        # right TextFields so accept/decline handlers stay shared with History.
        self._future_left_diff_texts: list[ft.Text] = []
        self._future_row_pill_hosts: list[ft.Container] = []
        self._future_row_stable_texts: list[str] = []
        # Compose text frozen when opening Compare (draft); left column diffs vs this, not live editor drift.
        self._compare_baseline_snapshot: str = ""
        self._compose_tab_inline_rename_active: bool = False
        self._compose_tab_rename_lock = asyncio.Lock()
        # Explorer: file row inline rename (⋯ menu), not dialog / double-click.
        self._tree_file_rename_target: Path | None = None
        self._tree_file_rename_field: ft.TextField | None = None
        self._tree_file_rename_lock = asyncio.Lock()

        self._compare_pdf_peer_snapshot_id: int | None = None
        self._compare_pdf_scroll_guard: bool = False
        self._compare_pdf_left_max_scroll: float = 1.0
        self._compare_pdf_right_max_scroll: float = 1.0
        self._plan_overlay_mode: bool = False
        self._plan_overlay_gen: int = 0
        self._plan_overlay_confidences: list[float] = []
        self._plan_side_by_side_mode: bool = False
        self._plan_layout_mode: str = "overlay"
        self._plan_overlay_defaults_set: bool = False
        self._text_review_user_layout_mode: str | None = None
        self._review_plan_document_id: int | None = None
        self._review_plan_version_id: int | None = None
        self._compose_plan_editor_collapsed: bool = False
        self._compose_plan_show_labels: bool = True
        self._plan_compare_show_labels: bool = False
        self._plan_compare_text_changes: list = []
        self._plan_compare_labels_nav_hosts: list = []
        self._skip_compose_plan_refresh_on_tab: bool = False
        self._compose_plan_page_index: int = 0
        self._compose_plan_focus_viewer = None
        self._compose_plan_document_id: int | None = None
        self._compose_plan_version_id: int | None = None
        self._fp_import = ft.FilePicker()
        self._fp_export_docx = ft.FilePicker()
        self._fp_export_plan_pdf = ft.FilePicker()
        self._fp_spell_dict = ft.FilePicker()
        self._fp_knowledge_export = ft.FilePicker()
        self._import_kind: str | None = None
        self._import_flow: str | None = None
        self._import_target_md: Path | None = None

        self._header_hide_gen: int = 0
        self._header_shell: ft.Container | None = None
        self._license_banner_host: ft.Container | None = None
        self._header_menu_open: int = 0
        self._header_chrome_hover: bool = False

        _hint_style = ft.TextStyle(color=config.ON_SURFACE_VARIANT)
        self.editor = ft.TextField(
            multiline=True,
            max_lines=None,
            min_lines=3,
            text_vertical_align=ft.VerticalAlignment.START,
            border=ft.InputBorder.NONE,
            filled=False,
            hint_text="Write…",
            hint_style=_hint_style,
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
                color=ui_theme.editor_text_color(),
            ),
            cursor_color=config.PRIMARY_COLOR,
            selection_color=config.SELECTION_OVERLAY,
            enable_interactive_selection=True,
            on_change=self._on_editor_change,
            on_selection_change=self._on_selection_change,
            on_focus=lambda _e: self._set_compose_editor_focused(True),
            on_blur=lambda _e: self._set_compose_editor_focused(False),
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
            text_style=ft.TextStyle(
                font_family="monospace",
                size=14,
                height=1.6,
                color=ui_theme.editor_text_color(),
            ),
            cursor_color=config.PRIMARY_COLOR,
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
        self._init_search_results_ui()
        self._compose_plan_load_gen = 0
        self._compose_plan_surface_key: tuple[int, bool, str] | None = None
        self._compose_plan_load_inflight_key: tuple[int, bool, str] | None = None
        self._compose_editor_area_gesture = ft.GestureDetector(
            content=self._editor_shell,
            on_secondary_tap_down=self._on_compose_editor_area_secondary_down,
            expand=True,
        )
        self._init_compose_selection_toolbar()
        self._compose_editor_and_actions_stack = ft.Container(
            expand=True,
            on_size_change=self._on_compose_editor_stack_resize,
            content=ft.Stack(
                [
                    ft.Container(
                        expand=True,
                        content=self._compose_editor_area_gesture,
                    ),
                    self._compose_selection_toolbar_host,
                ],
                expand=True,
                clip_behavior=ft.ClipBehavior.NONE,
            ),
        )
        self._compose_editor_shell_wrapped = ft.Container(
            content=self._compose_editor_and_actions_stack,
            expand=True,
        )
        self._focus_view_mode: Literal["wysiwyg", "source"] = "wysiwyg"
        self._wysiwyg_controller = None
        self._compose_wysiwyg_host = None
        self._compose_preview_md = ft.Markdown(
            value="",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
            soft_line_break=True,
            md_style_sheet=ui_theme.compose_preview_markdown_style_sheet(),
        )
        self._compose_writing_slot = ft.Container(
            expand=True,
            content=self._compose_editor_shell_wrapped,
        )
        self._compose_reading_inner = ft.Column(
            [
                self._compose_plan_host,
                self._compose_writing_slot,
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
                [
                    self._search_results_host,
                    self._compose_centered_row,
                ],
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
            bgcolor={ft.ControlState.DEFAULT: config.SURFACE},
            elevation=12,
            shadow_color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
            visual_density=ft.VisualDensity.COMPACT,
        )
        # Dense toolbar dropdown: line height 1.0 keeps label vertically aligned with the
        # trailing chevron; the tab strip below must be tall enough for Material input (~36px+).
        _tb_dd_text_style = ft.TextStyle(size=12, height=1.0, color=config.ON_SURFACE)
        _dd_opt_st = ui_theme.compare_candidate_dropdown_option_style()
        _tb_label_style = ft.TextStyle(size=12, color=config.ON_SURFACE_VARIANT)
        self._plan_baseline_dd_hover = False
        self._plan_candidate_dd_hover = False
        self._plan_compare_dropdown_focused = False
        self._plan_compare = plan_compare_panel.build_plan_compare_panel(
            on_baseline=lambda e: self.page.run_task(self._on_plan_pdf_baseline_async, e),
            on_candidate=lambda e: self.page.run_task(self._on_plan_pdf_candidate_async, e),
            on_hover_baseline=self._on_plan_baseline_dropdown_hover,
            on_hover_candidate=self._on_plan_candidate_dropdown_hover,
            on_baseline_focus=lambda _e: self._set_plan_compare_dropdown_focused(True),
            on_baseline_blur=lambda _e: self._set_plan_compare_dropdown_focused(False),
            on_candidate_focus=lambda _e: self._set_plan_compare_dropdown_focused(True),
            on_candidate_blur=lambda _e: self._set_plan_compare_dropdown_focused(False),
            dropdown_text_style=_tb_dd_text_style,
            menu_style=_dd_menu_style,
            option_button_style=_dd_opt_st,
            label_text_style=_tb_label_style,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
        )
        self._plan_compare_future = plan_compare_panel.build_plan_compare_panel(
            on_baseline=lambda e: self.page.run_task(self._on_plan_pdf_baseline_async, e),
            on_candidate=lambda e: self.page.run_task(self._on_plan_pdf_candidate_async, e),
            on_hover_baseline=self._on_plan_baseline_dropdown_hover,
            on_hover_candidate=self._on_plan_candidate_dropdown_hover,
            on_baseline_focus=lambda _e: self._set_plan_compare_dropdown_focused(True),
            on_baseline_blur=lambda _e: self._set_plan_compare_dropdown_focused(False),
            on_candidate_focus=lambda _e: self._set_plan_compare_dropdown_focused(True),
            on_candidate_blur=lambda _e: self._set_plan_compare_dropdown_focused(False),
            dropdown_text_style=_tb_dd_text_style,
            menu_style=_dd_menu_style,
            option_button_style=_dd_opt_st,
            label_text_style=_tb_label_style,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
        )
        self._compare_candidate_dropdown = ft.Dropdown(
            expand=True,
            dense=True,
            text_style=_tb_dd_text_style,
            filled=False,
            # Flet maps this to the *open menu* fill; TRANSPARENT reads as frosted/see-through.
            bgcolor=config.SURFACE,
            border=ft.InputBorder.NONE,
            border_width=0,
            content_padding=ft.padding.symmetric(horizontal=6, vertical=0),
            menu_style=_dd_menu_style,
            options=[],
            value=None,
            disabled=True,
            tooltip="Pick an older saved version (left column, deletions).",
            on_select=lambda e: self.page.run_task(self._on_compare_candidate_change_async, e),
            on_focus=lambda _e: self._set_compare_version_dd_focused(True),
            on_blur=lambda _e: self._set_compare_version_dd_focused(False),
        )
        # Rim matches tree search field: surface fill + grey outline; blue only on hover/focus (see history).
        self._compare_dropdown_hover_wrap = ft.Container(
            content=self._compare_candidate_dropdown,
            on_hover=self._on_compare_dropdown_container_hover,
            expand=True,
            bgcolor=config.SURFACE,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
            border=ft.Border.all(1, ui_theme.outline_muted()),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.padding.symmetric(horizontal=2, vertical=1),
        )
        self._compare_newer_dropdown = ft.Dropdown(
            expand=True,
            dense=True,
            text_style=_tb_dd_text_style,
            filled=False,
            bgcolor=config.SURFACE,
            border=ft.InputBorder.NONE,
            border_width=0,
            content_padding=ft.padding.symmetric(horizontal=6, vertical=0),
            menu_style=_dd_menu_style,
            options=[
                ft.dropdown.Option(
                    key=_COMPARE_KEY_CURRENT,
                    text="Current draft",
                    style=_dd_opt_st,
                )
            ],
            value=_COMPARE_KEY_CURRENT,
            disabled=True,
            tooltip="Pick the newer version (right column, insertions). Default is the current draft.",
            on_select=lambda e: self.page.run_task(self._on_compare_newer_change_async, e),
            on_focus=lambda _e: self._set_compare_version_dd_focused(True),
            on_blur=lambda _e: self._set_compare_version_dd_focused(False),
        )
        self._compare_newer_dropdown_hover_wrap = ft.Container(
            content=self._compare_newer_dropdown,
            on_hover=self._on_compare_newer_dropdown_container_hover,
            expand=True,
            bgcolor=config.SURFACE,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
            border=ft.Border.all(1, ui_theme.outline_muted()),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.padding.symmetric(horizontal=2, vertical=1),
        )
        self._review_baseline_dropdown = ft.Dropdown(
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
                    text="Current draft",
                    style=_dd_opt_st,
                )
            ],
            value=_COMPARE_KEY_CURRENT,
            disabled=True,
            visible=False,
            tooltip="Pick the current draft or a saved version for the left column.",
            on_select=lambda e: self.page.run_task(self._on_review_baseline_change_async, e),
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
            visible=False,
            tooltip="Pick draft vs draft, an AI proposal, or an import to review against the current draft.",
            on_select=lambda e: self.page.run_task(self._on_compare_candidate_change_async, e),
        )
        _compare_bulk_icon_style = ft.ButtonStyle(
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
            visual_density=ft.VisualDensity.COMPACT,
        )
        self._compare_approve_all_btn = ft.IconButton(
            ft.Icons.DONE_ALL,
            icon_size=18,
            icon_color=config.PRIMARY_COLOR,
            tooltip="Apply all paragraphs to the document",
            style=_compare_bulk_icon_style,
            visible=False,
            on_click=lambda _e: self.page.run_task(self._compare_approve_all_async),
        )
        self._compare_decline_all_btn = ft.IconButton(
            ft.Icons.CLOSE_ROUNDED,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Reset all paragraphs to match latest (left)",
            style=_compare_bulk_icon_style,
            visible=False,
            on_click=lambda _e: self.page.run_task(self._compare_decline_all_async),
        )
        # History tab label: simple text (dropdown lives in the tab body toolbar)
        self._compare_tab_label_host = ft.Container(
            content=ft.Text("History", size=14, color=config.ON_SURFACE_VARIANT),
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
        self._compare_plan_focus_left: object | None = None
        self._compare_plan_focus_right: object | None = None
        self._compare_plan_overlay_focus: object | None = None
        self._compare_plan_focus_left_slot = ft.Container(expand=True)
        self._compare_plan_focus_right_slot = ft.Container(expand=True)
        self._compare_plan_overlay_focus_slot = ft.Container(expand=True)
        self._plan_compare.overlay_list.visible = False
        self._compare_pdf_right_column = ft.Container(
            content=self._compare_plan_focus_right_slot,
            expand=True,
        )
        self._compare_pdf_overlay_host = ft.Container(
            content=self._compare_plan_overlay_focus_slot,
            expand=True,
            visible=False,
            on_size_change=self._on_compare_plan_overlay_host_size,
        )
        self._compare_pdf_split_row = ft.Row(
            [
                ft.Container(
                    content=self._compare_plan_focus_left_slot,
                    expand=True,
                    border=ft.border.all(1, ui_theme.outline_muted(alpha=0.38)),
                    border_radius=8,
                ),
                ft.Container(
                    content=self._compare_pdf_right_column,
                    expand=True,
                    border=ft.border.all(1, ui_theme.outline_muted(alpha=0.38)),
                    border_radius=8,
                ),
            ],
            expand=True,
            spacing=8,
        )
        self._compare_pdf_layer = ft.Container(
            content=ft.Column(
                [self._compare_pdf_split_row, self._compare_pdf_overlay_host],
                expand=True,
                spacing=8,
            ),
            expand=True,
            visible=False,
            on_size_change=self._on_compare_plan_pdf_layer_size,
        )
        self._compare_editor_holder = ft.Container(content=self._compare_editor, visible=False, height=0)
        def _make_result_card_overlay() -> ft.Container:
            return ft.Container(
                visible=False,
                width=_RESULT_CARD_W,
                bgcolor=ui_theme.result_card_bg(),
                border=ft.border.all(1, ui_theme.result_card_border()),
                border_radius=10,
                padding=ft.padding.all(12),
                shadow=ui_theme.soft_elevation_shadow(),
                top=0,
                left=4,
                on_hover=self._on_result_card_hover,
                content=ft.Column([], tight=True, spacing=6),
            )

        # Floating card on hover over Analyse eval symbol (History / compare paragraph stack).
        self._result_card_overlay = _make_result_card_overlay()
        # Same chrome for Future tab stack (a control cannot have two parents).
        self._future_result_card_overlay = _make_result_card_overlay()
        self._compare_body_stack = ft.Stack(
            controls=[
                self._compare_paragraph_layer,
                self._compare_pdf_layer,
                self._result_card_overlay,
            ],
            expand=True,
        )
        # History tab body (plan PDF bar lives in _toolbar_history under the main tabs)
        self._compare_tab_body = ft.Column(
            [
                ft.Row(
                    [self._compare_body_stack],
                    expand=True,
                ),
                self._compare_editor_holder,
            ],
            expand=True,
            spacing=0,
        )
        # Review Difference | Impact strip — lives inside the Review TabBarView page (below filename band).
        self._review_subtab_change_btn = self._build_review_subtab_button("Difference", 0)
        self._review_subtab_impact_btn = self._build_review_subtab_button("Impact", 1)
        self._review_subtab_strip = ft.Container(
            visible=config.RAG_SYSTEM,
            bgcolor=config.SURFACE,
            content=ft.Column(
                [
                    ft.Row(
                        [self._review_subtab_change_btn, self._review_subtab_impact_btn],
                        spacing=0,
                        expand=True,
                    ),
                    ft.Divider(
                        height=1,
                        thickness=1,
                        color=ui_theme.outline_muted(alpha=0.22),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        )

        # Future tab: separate listview and body
        self._future_rows_listview = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
        )
        self._future_paragraph_layer = ft.Container(content=self._future_rows_listview, expand=True)
        self._future_pdf_left_lv = ft.ListView(
            expand=True,
            spacing=8,
            padding=ft.padding.all(8),
            on_scroll=self._on_future_pdf_scroll_left,
        )
        self._future_pdf_right_lv = ft.ListView(
            expand=True,
            spacing=6,
            padding=ft.padding.all(8),
            on_scroll=self._on_future_pdf_scroll_right,
        )
        self._future_plan_focus_left: object | None = None
        self._future_plan_focus_right: object | None = None
        self._future_plan_side_by_side_pair: object | None = None
        self._future_plan_focus_single: object | None = None
        self._future_plan_overlay_focus: object | None = None
        self._future_plan_focus_left_slot = ft.Container(expand=True)
        self._future_plan_focus_right_slot = ft.Container(expand=True)
        self._future_plan_side_by_side_slot = ft.Container(expand=True)
        self._future_plan_single_slot = ft.Container(expand=True)
        self._future_plan_overlay_focus_slot = ft.Container(expand=True)
        self._future_plan_single_host = ft.Container(
            content=self._future_plan_single_slot,
            expand=True,
            visible=False,
            on_size_change=self._on_future_plan_single_host_size,
        )
        self._future_plan_overlay_host = ft.Container(
            content=self._future_plan_overlay_focus_slot,
            expand=True,
            visible=False,
            on_size_change=self._on_future_plan_overlay_host_size,
        )
        self._future_pdf_split_row = ft.Container(
            content=self._future_plan_side_by_side_slot,
            expand=True,
            on_size_change=self._on_future_plan_split_row_size,
        )
        self._future_pdf_layer = ft.Container(
            content=ft.Column(
                [
                    self._plan_compare_future.host,
                    self._future_plan_single_host,
                    self._future_pdf_split_row,
                    self._future_plan_overlay_host,
                ],
                expand=True,
                spacing=4,
            ),
            expand=True,
            visible=False,
        )
        self._future_pdf_import_md_tf = ft.TextField(
            multiline=True,
            max_lines=None,
            min_lines=1,
            border=ft.InputBorder.NONE,
            filled=False,
            dense=True,
            text_size=COMPARE_COL_FONT_SIZE,
            text_style=ft.TextStyle(
                font_family="monospace",
                size=COMPARE_COL_FONT_SIZE,
                height=COMPARE_COL_LINE_HEIGHT,
                color=ui_theme.editor_text_color(),
            ),
            cursor_color=config.PRIMARY_COLOR,
            selection_color=config.SELECTION_OVERLAY,
            content_padding=ft.padding.all(8),
            expand=True,
            enable_interactive_selection=True,
            on_change=self._on_future_pdf_import_md_change,
        )
        self._future_body_stack = ft.Stack(
            controls=[
                self._future_paragraph_layer,
                self._future_pdf_layer,
                self._future_result_card_overlay,
            ],
            expand=True,
        )
        self._pill_row_impact = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._impact_summary_cache: str = ""
        self._init_impact_ui_fields()
        self._review_change_panel = ft.Container(
            expand=True,
            content=ft.Column(
                [ft.Row([self._future_body_stack], expand=True)],
                expand=True,
                spacing=0,
            ),
        )
        self._impact_status_text = ft.Text(
            "",
            size=13,
            color=config.ON_SURFACE_VARIANT,
            text_align=ft.TextAlign.CENTER,
        )
        self._impact_results_list = ft.ListView(
            expand=True,
            spacing=4,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            visible=False,
        )
        self._impact_summary_text = ft.Text(
            "",
            size=12,
            color=config.ON_SURFACE,
            selectable=True,
        )
        self._impact_summary_container = ft.Container(
            visible=False,
            padding=ft.padding.all(10),
            border=ft.border.all(1, config.OUTLINE),
            border_radius=8,
            margin=ft.margin.only(top=4, left=8, right=8, bottom=8),
            content=ft.Column(
                [
                    ft.Text(
                        "Summary",
                        size=11,
                        weight=ft.FontWeight.W_600,
                        color=config.ON_SURFACE_VARIANT,
                    ),
                    self._impact_summary_text,
                ],
                spacing=4,
                tight=True,
            ),
        )
        self._impact_para_listview = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.symmetric(horizontal=4, vertical=2),
        )
        self._impact_result_card_overlay = ft.Container(
            visible=False,
            right=0,
            top=4,
            width=320,
            padding=ft.padding.all(10),
            bgcolor=config.SURFACE,
            border=ft.border.all(1, config.OUTLINE),
            border_radius=10,
            shadow=ft.BoxShadow(blur_radius=8, color=ft.Colors.with_opacity(0.18, ft.Colors.BLACK)),
            content=ft.Column([], spacing=4, tight=True, scroll=ft.ScrollMode.AUTO),
            on_hover=lambda e: self._on_impact_result_card_hover(e),
        )
        self._review_impact_panel = ft.Container(
            expand=False,
            visible=False,
            content=ft.Column(
                [
                    ft.Container(
                        content=self._impact_status_text,
                        alignment=ft.Alignment.CENTER,
                        padding=ft.padding.symmetric(vertical=6),
                    ),
                    ft.Container(
                        expand=True,
                        alignment=ft.Alignment.TOP_CENTER,
                        content=ft.Container(
                            width=680,
                            expand=True,
                            content=ft.Stack(
                                [self._impact_para_listview, self._impact_result_card_overlay],
                                expand=True,
                            ),
                        ),
                    ),
                ],
                expand=True,
                spacing=0,
            ),
        )

        self._compose_tab_filename_text = ft.Text(
            "—",
            size=14,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            color=config.ON_SURFACE_VARIANT,
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
            border_color=config.OUTLINE,
            focused_border_color=config.PRIMARY_COLOR,
            cursor_color=config.PRIMARY_COLOR,
            selection_color=config.SELECTION_OVERLAY,
            content_padding=ft.padding.only(left=0, right=4, bottom=2, top=0),
            on_submit=self._on_compose_tab_rename_field_submit,
            on_blur=self._on_compose_tab_rename_field_blur,
        )
        # Static (non-editable) extension shown next to the rename field so the
        # user sees the full filename but cannot change the extension.
        self._compose_tab_filename_suffix_text = ft.Text(
            "",
            size=14,
            color=config.ON_SURFACE_VARIANT,
            visible=False,
        )
        self._focus_preview_toggle_btn = ft.IconButton(
            icon=ft.Icons.CODE,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Markdown source",
            style=ft.ButtonStyle(padding=ft.padding.all(2)),
            visible=False,
            on_click=lambda _e: self._toggle_focus_compose_mode(),
        )
        self._plan_layout_menu_btn = ft.PopupMenuButton(
            icon=ft.Icons.LAYERS_OUTLINED,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Overlay old and new",
            style=ft.ButtonStyle(padding=ft.padding.all(2)),
            visible=False,
            menu_position=ft.PopupMenuPosition.UNDER,
            items=[],
        )
        _plan_annot_btn_style = action_rail_icon_button_style()
        self._plan_review_comment_btn = ft.IconButton(
            icon=ft.Icons.CHAT_BUBBLE_OUTLINE,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Place comment",
            style=_plan_annot_btn_style,
            visible=False,
            disabled=True,
            on_click=self._on_plan_review_comment_toggle,
        )
        self._plan_compare_labels_btn = ft.IconButton(
            icon=ft.Icons.TEXT_FIELDS,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Extracted text",
            style=_plan_annot_btn_style,
            visible=False,
            disabled=True,
            on_click=self._on_plan_compare_toggle_labels,
        )
        self._plan_region_impact_btn = ft.TextButton(
            "Region impact",
            visible=False,
            disabled=True,
            on_click=lambda _e: self.page.run_task(self._run_plan_region_impact_batch_async),
        )
        # Stable height: preview IconButton is taller than the filename text; toggling
        # it must not resize this row or the label shifts when changing main tabs.
        self._compose_tab_filename_row = ft.Container(
            height=48,
            alignment=ft.Alignment(0, 0),
            content=ft.Row(
                [
                    self._compose_tab_filename_hit,
                    self._compose_tab_filename_field,
                    self._compose_tab_filename_suffix_text,
                    self._plan_layout_menu_btn,
                    self._plan_region_impact_btn,
                    self._focus_preview_toggle_btn,
                ],
                tight=True,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        # Under main History | Focus | Review tabs on every mode (rename targets this row).
        self._workspace_filename_band = ft.Container(
            bgcolor=config.SURFACE,
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                [
                    ft.Container(expand=True),
                    self._compose_tab_filename_row,
                    ft.Container(expand=True),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
        )
        # ── Tab-specific toolbar (below tab bar) ─────────────────────────────
        _tb_pad = ft.padding.symmetric(horizontal=12, vertical=4)

        # History: markdown version row + plan PDF row (same toolbar band)
        self._toolbar_history_md_row = ft.Row(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text("Older:", style=_tb_label_style),
                            self._compare_dropdown_hover_wrap,
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
                    expand=1,
                ),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text("Newer:", style=_tb_label_style),
                            self._compare_newer_dropdown_hover_wrap,
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
                    expand=1,
                ),
            ],
            spacing=HISTORY_COMPARE_DROPDOWN_COLUMNS_GAP_PX,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )
        self._toolbar_history = ft.Container(
            content=ft.Column(
                [
                    self._toolbar_history_md_row,
                    self._plan_compare.host,
                ],
                spacing=4,
                tight=True,
                expand=True,
            ),
            padding=_tb_pad,
            expand=True,
        )

        self._toolbar_present_spacer = ft.Container(height=0, expand=True)
        self._toolbar_review_spacer = ft.Container(height=0, expand=True)

        # Only History shows Older/Newer here; Focus uses filename band only; Review uses rows below.
        self._tab_toolbar_inner = ft.Container(
            content=self._toolbar_present_spacer,
            height=0,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self._tab_toolbar = ft.Container(
            visible=False,
            bgcolor=config.SURFACE,
            content=ft.Column(
                [
                    self._tab_toolbar_inner,
                    ft.Divider(
                        height=1,
                        thickness=1,
                        color=ui_theme.outline_muted(alpha=0.22),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        )
        self._review_baseline_chrome_col = ft.Container(
            expand=1,
            content=ft.Row(
                [
                    ft.Text("Current:", style=_tb_label_style),
                    self._review_baseline_dropdown,
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            ),
        )
        self._review_difference_chrome_row = ft.Container(
            visible=True,
            bgcolor=config.SURFACE,
            padding=_tb_pad,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._review_baseline_chrome_col,
                            ft.Container(
                                content=ft.Row(
                                    [
                                        ft.Text("Candidate", style=_tb_label_style),
                                        self._review_candidate_dropdown,
                                        self._compare_approve_all_btn,
                                        self._compare_decline_all_btn,
                                    ],
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    expand=True,
                                ),
                                expand=1,
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True,
                    ),
                    ft.Divider(
                        height=1,
                        thickness=1,
                        color=ui_theme.outline_muted(alpha=0.22),
                    ),
                ],
                spacing=0,
                tight=True,
            ),
        )
        # Column (not Stack): two expand=True siblings in a Stack can starve the ListView of height
        # when toggling Difference vs Impact. Only the active subpanel gets expand=True.
        self._review_subpanels_column = ft.Column(
            [self._review_change_panel, self._review_impact_panel],
            expand=True,
            spacing=0,
        )
        _future_tab_col_children: list[ft.Control] = [
            self._review_subtab_strip,
            self._review_difference_chrome_row,
            self._review_subpanels_column,
        ]
        self._plan_compare_future.set_bar_visible(False)
        self._future_tab_body = ft.Container(
            expand=True,
            content=ft.Column(
                _future_tab_col_children,
                expand=True,
                spacing=0,
            ),
        )
        # ─────────────────────────────────────────────────────────────────────

        self._compose_tab_label_row = ft.Row(
            [ft.Text("Focus Area", size=14, color=config.ON_SURFACE_VARIANT)],
            tight=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        # Future tab label: "Future" + bulk approve/decline buttons
        _future_tab_label_row = ft.Row(
            [ft.Text("Review", size=14, color=config.ON_SURFACE_VARIANT)],
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
            indicator_color=config.HIGHLIGHT,
            divider_color=ui_theme.outline_muted(alpha=0.28),
            label_color=config.ON_SURFACE,
            unselected_label_color=config.ON_SURFACE_VARIANT,
            overlay_color=ft.Colors.with_opacity(0.06, config.ON_SURFACE),
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
            [
                self._sticky_tab_header,
                self._workspace_filename_band,
                self._tab_toolbar,
                self._tab_bar_view,
            ],
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

        _sym_path = (
            config.APP_SYMBOL_SVG
            if config.APP_SYMBOL_SVG.is_file()
            else config.APP_SYMBOL_PNG
        )
        self.app_symbol = ft.Image(
            src=str(_sym_path.resolve()),
            width=22,
            height=22,
            fit=ft.BoxFit.CONTAIN,
            # Tint to title color: white in dark mode, ink in light (SRC_IN works for SVG too).
            color=config.ON_SURFACE,
            color_blend_mode=ft.BlendMode.SRC_IN,
        )
        self.filename_text = ft.Text(
            "iterthink - No file",
            size=16,
            weight=ft.FontWeight.W_500,
            color=config.ON_SURFACE,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1,
        )
        self.dirty_dot = ft.Text(
            "•",
            size=18,
            weight=ft.FontWeight.W_700,
            color=config.PRIMARY_COLOR,
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
        self._content_tree_gen: int = 0

        self.tree_column = ft.Column(spacing=0, tight=True, scroll=ft.ScrollMode.AUTO, expand=True)
        self.content_tree_column = ft.Column(
            spacing=0, tight=True, scroll=ft.ScrollMode.AUTO, expand=True
        )
        self._left_sidebar_tab = 0
        self._left_sidebar_toolbar_band = None
        self._left_sidebar_tree_well = None
        self._left_sidebar_content_well = None
        self._tree_sort_mode = "name_az"
        self._tree_explorer_overflow_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_size=KI_TAB_ICON_PX,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Explorer menu",
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
                    content=ft.Text("Create project…", size=13),
                    on_click=lambda _e: self._show_create_project_dialog(),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Import…", size=13),
                    on_click=lambda _e: self.page.run_task(self._tree_import_new_clicked),
                ),
                ft.PopupMenuItem(),
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
            hint_text="Search… (/f filenames, /p project)",
            dense=True,
            filled=False,
            border=ft.InputBorder.NONE,
            text_size=12,
            color=config.ON_SURFACE,
            hint_style=_hint_style,
            cursor_color=config.PRIMARY_COLOR,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=0),
            expand=True,
            on_change=self._on_tree_search_change,
        )
        _tree_search_bar = ft.Container(
            expand=True,
            height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
            bgcolor=config.SIDEBAR_SURFACE,
            border_radius=float(SIDEBAR_INNER_BORDER_RADIUS_PX),
            border=ft.Border.all(1, ui_theme.outline_muted()),
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=self.tree_search_field,
        )
        self._tree_search_bar = _tree_search_bar

        def _tree_search_rim(focused: bool) -> None:
            _tree_search_bar.border = ft.Border.all(
                1, config.PRIMARY_COLOR if focused else ui_theme.outline_muted()
            )
            if _ctrl_on_page(_tree_search_bar):
                _tree_search_bar.update()

        self.tree_search_field.on_focus = lambda _e: _tree_search_rim(True)
        self.tree_search_field.on_blur = lambda _e: _tree_search_rim(False)
        self._sync_rag_search_ui()

        self._init_content_find_replace_ui(_hint_style)

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
        self._ki_topic_index: int = 1
        self._comment_para_index: int | None = None
        self._comment_edit_mode: bool = False
        self._ki_comment_pick_mode: bool = False
        self._chat_api_messages: list[dict[str, str]] = []
        self._init_sidebar_llm_control()

        # ----- Per-paragraph Analyse checks (KI Analyse tab) -----
        # Active check id whose symbols populate the Compare row eval cells.
        self._active_check_id: str | None = None
        # Per-check results aligned with current candidate paragraph indices.
        self._check_results: dict[str, list[dict | None]] = {}
        # Per-check running flag; True while a paragraph-by-paragraph run is in progress.
        self._check_running: dict[str, bool] = {}
        # Monotonic generation per check; cancels stale background runs.
        self._check_run_gen: dict[str, int] = {}
        # Aligned-pair fingerprints (baseline+candidate per row) for in-memory invalidation.
        self._check_para_hashes: list[str] = []
        # Eval-cell host containers, parallel to _compare_right_fields, for O(1) refresh.
        self._compare_eval_hosts: list[ft.Container] = []
        # Floating result-card overlay state.
        self._result_card_visible_for: tuple[str, int] | None = None
        self._result_card_pinned_ui_idx: int | None = None
        self._result_card_hide_gen: int = 0

        self._pill_row_discuss = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._pill_row_change = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._pill_row_analyse = ft.Row(spacing=4, wrap=True, run_spacing=4)
        self._analyse_buttons: dict[str, ft.FilledButton] = {}
        self._analyse_button_progress: dict[str, ft.ProgressRing] = {}
        self._analyse_button_count: dict[str, ft.Text] = {}
        self._impact_analyse_section = ft.Container(
            visible=False,
            padding=ft.padding.only(top=4),
            content=ft.Column(
                [self._pill_row_impact],
                spacing=2,
                tight=True,
            ),
        )
        from .ki_act_workflows import build_ki_act_panel

        self._ki_act_panel = build_ki_act_panel(studio=self, page=self.page)
        self._ki_act_container = ft.Container(
            padding=ft.padding.symmetric(
                horizontal=4,
                vertical=KI_TAB_PAGE_PAD_V_PX,
            ),
            content=self._ki_act_panel,
            expand=True,
        )
        self._comment_heading = ft.Text(
            "",
            size=13,
            weight=ft.FontWeight.W_600,
            color=config.ON_SURFACE,
            expand=True,
        )
        self._comment_edit_btn = ft.IconButton(
            ft.Icons.EDIT_OUTLINED,
            icon_size=18,
            tooltip="Edit comment",
            icon_color=config.ON_SURFACE_VARIANT,
            style=ft.ButtonStyle(
                padding=ft.padding.all(2),
                visual_density=ft.VisualDensity.COMPACT,
            ),
        )
        self._comment_header_row = ft.Row(
            [self._comment_heading, self._comment_edit_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )
        self._comment_body_display = ft.Text(
            "",
            size=12,
            selectable=True,
            color=config.ON_SURFACE,
        )
        self._comment_body_edit = ft.TextField(
            multiline=True,
            min_lines=4,
            max_lines=14,
            expand=True,
            dense=True,
            filled=True,
            fill_color=config.SURFACE,
            focused_bgcolor=config.SURFACE,
            bgcolor=config.SURFACE,
            border_radius=8,
            text_size=12,
            color=config.ON_SURFACE,
            border_color=ui_theme.outline_muted(),
            focused_border_color=config.PRIMARY_COLOR,
            cursor_color=config.PRIMARY_COLOR,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=6),
            visible=False,
        )
        self._comment_save_btn = ft.FilledButton("Save", visible=False)
        self._comment_cancel_btn = ft.TextButton("Cancel", visible=False)

        def _on_comment_edit(_e: ft.ControlEvent | None = None) -> None:
            self._comment_edit_mode = True
            self._comment_body_edit.value = self._comment_body_display.value or ""
            self._comment_body_edit.visible = True
            self._comment_body_display.visible = False
            self._comment_edit_btn.visible = False
            self._comment_save_btn.visible = True
            self._comment_cancel_btn.visible = True
            if _ctrl_on_page(self._comment_body_edit):
                self._comment_body_edit.update()
            if _ctrl_on_page(self._comment_body_display):
                self._comment_body_display.update()
            for b in (self._comment_edit_btn, self._comment_save_btn, self._comment_cancel_btn):
                if _ctrl_on_page(b):
                    b.update()

        def _on_comment_cancel(_e: ft.ControlEvent | None = None) -> None:
            self._comment_edit_mode = False
            self._comment_body_edit.visible = False
            self._comment_body_display.visible = True
            self._comment_edit_btn.visible = bool((self._comment_body_display.value or "").strip())
            self._comment_save_btn.visible = False
            self._comment_cancel_btn.visible = False
            if _ctrl_on_page(self._comment_body_edit):
                self._comment_body_edit.update()
            if _ctrl_on_page(self._comment_body_display):
                self._comment_body_display.update()
            for b in (self._comment_edit_btn, self._comment_save_btn, self._comment_cancel_btn):
                if _ctrl_on_page(b):
                    b.update()

        self._comment_edit_btn.on_click = _on_comment_edit
        self._comment_save_btn.on_click = lambda _e: self.page.run_task(self._save_ki_paragraph_comment_async)
        self._comment_cancel_btn.on_click = _on_comment_cancel
        self._ki_comment_add_btn = ft.IconButton(
            ft.Icons.ADD,
            icon_size=18,
            tooltip="Add comment",
            icon_color=config.ON_SURFACE_VARIANT,
            style=ft.ButtonStyle(
                padding=ft.padding.all(2),
                visual_density=ft.VisualDensity.COMPACT,
            ),
            on_click=self._on_ki_comment_add_click,
        )
        self._ki_comments_header = ft.Row(
            [
                ft.Text(
                    "Comments",
                    size=13,
                    weight=ft.FontWeight.W_600,
                    color=config.ON_SURFACE,
                    expand=True,
                ),
                self._ki_comment_add_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )
        self._ki_comments_list = ft.ListView(
            expand=True,
            spacing=12,
            padding=ft.padding.symmetric(horizontal=4, vertical=4),
        )
        self._ki_comments_detail = ft.Column(
            [
                self._comment_header_row,
                self._comment_body_display,
                self._comment_body_edit,
                ft.Row(
                    [self._comment_save_btn, self._comment_cancel_btn],
                    spacing=8,
                    tight=True,
                ),
            ],
            spacing=8,
            tight=True,
            visible=False,
        )
        self._ki_comments_panel = ft.Column(
            [self._ki_comments_header, self._ki_comments_detail, self._ki_comments_list],
            spacing=8,
            expand=True,
        )
        self._ki_comments_container = ft.Container(
            expand=True,
            padding=ft.padding.symmetric(
                horizontal=4,
                vertical=KI_TAB_PAGE_PAD_V_PX,
            ),
            content=self._ki_comments_panel,
        )
        # Pages are kept separately so we can hot-swap them as the active tab
        # changes. A plain Container (without bounded height) lets the wrap Row
        # render its full intrinsic height instead of being clipped by a
        # TabBarView's PageView constraint.
        self._ki_tab_pages: list[ft.Control] = [
            self._ki_comments_container,
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
                content=ft.Column(
                    [self._pill_row_analyse, self._impact_analyse_section],
                    spacing=0,
                    tight=True,
                ),
            ),
            self._ki_act_container,
        ]
        self._ki_tab_bar_view = ft.Container(
            expand=bool(self._ki_topic_index == KI_TOPIC_COMMENTS),
            content=self._ki_tab_pages[self._ki_topic_index],
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
                (ft.Icons.CHAT_BUBBLE_OUTLINE, "Comments"),
                (KI_TOPIC_STRIP_DISCUSS_ICON, "Discuss"),
                (ft.Icons.MODE_EDIT, "Change"),
                (ft.Icons.INSIGHTS, "Analyse"),
                (ft.Icons.PRECISION_MANUFACTURING, "Act"),
            ]
        ):
            sel = i == self._ki_topic_index
            ib = ft.IconButton(
                icon=ic,
                tooltip=tip,
                icon_size=KI_TAB_ICON_PX,
                icon_color=config.PRIMARY_COLOR if sel else config.ON_SURFACE_VARIANT,
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
                        config.HIGHLIGHT if sel else ft.Colors.TRANSPARENT,
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
        self._ki_topic_tabs = ft.Container(
            expand=bool(self._ki_topic_index == KI_TOPIC_COMMENTS),
            padding=ft.padding.only(top=float(KI_TAB_BAR_TO_PILLS_GAP_PX)),
            content=self._ki_tab_bar_view,
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
            fill_color=config.SURFACE,
            focused_bgcolor=config.SURFACE,
            bgcolor=config.SURFACE,
            border_radius=8,
            expand=True,
            text_size=12,
            color=config.ON_SURFACE,
            hint_style=_hint_style,
            border_color=ui_theme.outline_muted(),
            focused_border_color=config.PRIMARY_COLOR,
            cursor_color=config.PRIMARY_COLOR,
            content_padding=ft.padding.symmetric(horizontal=8, vertical=6),
            on_submit=lambda e: self.page.run_task(self._on_chat_send_click, e),
        )
        self._chat_send_btn = ft.IconButton(
            icon=ft.Icons.SEND,
            icon_size=20,
            tooltip="Send",
            icon_color=config.PRIMARY_COLOR,
            style=ft.ButtonStyle(padding=ft.padding.all(4)),
            on_click=lambda e: self.page.run_task(self._on_chat_send_click, e),
        )
        _tier_ix = KI_TIERS.index(normalize_ki_tier(self.ki_tier))
        self._ki_tier_tabs = build_llm_tier_tabs(
            selected_index=_tier_ix,
            on_change=self._on_ki_tier_tabs_change,
            icon_size=KI_TIER_TAB_ICON_PX,
            tab_bar_height=float(SIDEBAR_TOOLBAR_ROW_H_PX),
        )
        self._ensure_ki_tier_tabs_enabled()
        self._privacy_shield_icon = ft.Icon(
            ft.Icons.SHIELD,
            size=KI_TIER_TAB_ICON_PX,
            color=config.HIGHLIGHT,
        )
        sync_privacy_shield_icon(
            self._privacy_shield_icon,
            tier=self.ki_tier,
            reinject=config.PRIVACY_SHIELD_REINJECT,
        )
        self._token_cost_label = build_token_cost_label(size=11)
        self._sync_token_cost_display()
        self._ki_tier_row = ft.Row(
            [
                ft.Container(content=self._ki_tier_tabs, expand=True),
                self._token_cost_label,
                self._privacy_shield_icon,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._chat_model_options: list[str] = []
        self._chat_input_row = ft.Row(
            [self._chat_input, self._chat_send_btn],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        )
        self._impact_run_dock = ft.Container(
            visible=False,
            padding=ft.padding.only(top=4),
            content=ft.Row(
                [self._impact_run_btn],
                expand=True,
                alignment=ft.MainAxisAlignment.START,
            ),
        )
        self._chat_composer = ft.Container(
            padding=ft.padding.symmetric(horizontal=0, vertical=6),
            bgcolor=ft.Colors.TRANSPARENT,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    self._ki_tier_row,
                    self._chat_input_row,
                    self._impact_run_dock,
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
        self._impact_ki_context_title = ft.Text(
            "Context files (.md)",
            size=11,
            weight=ft.FontWeight.W_600,
            color=config.ON_SURFACE_VARIANT,
            visible=False,
        )
        self._impact_ki_context_scroll = ft.Column(
            [],
            scroll=ft.ScrollMode.AUTO,
            height=200,
            spacing=0,
            tight=True,
            visible=False,
        )
        self._impact_ki_context_panel = ft.Container(
            visible=False,
            padding=ft.padding.only(bottom=4),
            content=ft.Column(
                [self._impact_ki_context_title, self._impact_ki_context_scroll],
                spacing=4,
                tight=True,
            ),
        )
        self._impact_summary_right_text = ft.Text(
            "",
            size=11,
            color=config.ON_SURFACE,
            selectable=True,
        )
        self._impact_summary_right = ft.Container(
            visible=False,
            padding=ft.padding.all(8),
            border=ft.border.all(1, ui_theme.outline_muted(alpha=0.35)),
            border_radius=8,
            margin=ft.margin.only(bottom=4),
            content=ft.Column(
                [
                    ft.Text(
                        "Impact summary",
                        size=10,
                        weight=ft.FontWeight.W_600,
                        color=config.ON_SURFACE_VARIANT,
                    ),
                    self._impact_summary_right_text,
                ],
                spacing=4,
                tight=True,
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
                    self._impact_ki_context_panel,
                    self._impact_summary_right,
                    self._right_chat_section,
                ],
                expand=True,
                spacing=4,
            ),
        )
        self._right_ki_column = self._ki_sidebar_well

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

    def _refresh_dirty_state_ui(self) -> None:
        """Lightweight dirty indicator for the keystroke hot path (no tab/preview relayout)."""
        visible = bool(self.current_path) and self._is_dirty()
        if self.dirty_dot.visible == visible:
            return
        self.dirty_dot.visible = visible
        if _ctrl_on_page(self.dirty_dot):
            self.dirty_dot.update()

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
        self._refresh_compose_tab_label(apply_preview_mode=False)
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
            self.page.services.append(self._fp_export_docx)
            self.page.services.append(self._fp_export_plan_pdf)
            self.page.services.append(self._fp_spell_dict)
            self.page.services.append(self._fp_knowledge_export)
            self.page.update()

    def refresh_ollama_client(self) -> None:
        self.ollama = AsyncClient(host=config.OLLAMA_HOST) if config.OLLAMA_HOST else AsyncClient()

    def _apply_ki_tier_tab_bar_theme(self) -> None:
        """KI composer tier strip (Local / Office / Cloud): tab chrome follows appearance."""
        tabs = getattr(self, "_ki_tier_tabs", None)
        if tabs is None:
            return
        body = getattr(tabs, "content", None)
        if not isinstance(body, ft.Column) or not body.controls:
            return
        tier_bar = body.controls[0]
        if not isinstance(tier_bar, ft.TabBar):
            return
        tier_bar.indicator_color = config.PRIMARY_COLOR
        tier_bar.divider_color = ui_theme.outline_muted(alpha=0.22)
        tier_bar.label_color = config.ON_SURFACE
        tier_bar.unselected_label_color = config.ON_SURFACE_VARIANT
        tier_bar.overlay_color = ft.Colors.with_opacity(0.06, config.ON_SURFACE)
        if _ctrl_on_page(tier_bar):
            tier_bar.update()
        self._sync_ki_tier_tab_icons()
        self._sync_token_cost_display()

    def apply_config_theme(self) -> None:
        self.page.theme_mode = ft.ThemeMode.LIGHT if config.IS_LIGHT else ft.ThemeMode.DARK
        self.page.bgcolor = config.PAGE_BG
        ec = ui_theme.editor_text_color()
        self.editor.text_style = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=ec,
        )
        self.editor.cursor_color = config.PRIMARY_COLOR
        self.editor.selection_color = config.SELECTION_OVERLAY
        self._compare_editor.text_style = ft.TextStyle(
            font_family="monospace",
            size=14,
            height=1.6,
            color=ec,
        )
        self._compare_editor.cursor_color = config.PRIMARY_COLOR
        self._compare_editor.selection_color = config.SELECTION_OVERLAY
        self.dirty_dot.color = config.PRIMARY_COLOR
        self.filename_text.color = config.ON_SURFACE
        self.app_symbol.color = config.ON_SURFACE
        self.app_symbol.color_blend_mode = ft.BlendMode.SRC_IN
        dd_ts = ft.TextStyle(size=12, height=1.0, color=config.ON_SURFACE)
        self._compare_candidate_dropdown.text_style = dd_ts
        self._compare_newer_dropdown.text_style = dd_ts
        self._review_baseline_dropdown.text_style = dd_ts
        self._review_candidate_dropdown.text_style = dd_ts
        self._compare_candidate_dropdown.bgcolor = config.SURFACE
        self._compare_newer_dropdown.bgcolor = config.SURFACE
        self._review_baseline_dropdown.bgcolor = config.SURFACE
        self._compare_candidate_dropdown.menu_style = ft.MenuStyle(
            bgcolor=config.SURFACE,
            elevation=12,
            shadow_color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
            visual_density=ft.VisualDensity.COMPACT,
        )
        self._compare_newer_dropdown.menu_style = self._compare_candidate_dropdown.menu_style
        self._review_baseline_dropdown.menu_style = self._compare_candidate_dropdown.menu_style
        self._review_candidate_dropdown.menu_style = self._compare_candidate_dropdown.menu_style
        _lbl = ft.TextStyle(size=12, color=config.ON_SURFACE_VARIANT)
        self._plan_compare.baseline_dd.text_style = dd_ts
        self._plan_compare.candidate_dd.text_style = dd_ts
        self._plan_compare.baseline_dd.bgcolor = config.SURFACE
        self._plan_compare.candidate_dd.bgcolor = config.SURFACE
        self._plan_compare.baseline_dd.menu_style = self._compare_candidate_dropdown.menu_style
        self._plan_compare.candidate_dd.menu_style = self._compare_candidate_dropdown.menu_style
        self._plan_compare.baseline_wrap.bgcolor = config.SURFACE
        self._plan_compare.candidate_wrap.bgcolor = config.SURFACE
        self._plan_compare.baseline_label.style = _lbl
        self._plan_compare.candidate_label.style = _lbl
        self._sync_side_panel_chrome()
        self.center_panel.bgcolor = config.SURFACE
        self._apply_compare_candidate_dropdown_tab_chrome()
        self._ki_topic_top_bar.bgcolor = config.SIDEBAR_SURFACE
        self._ki_sidebar_well.bgcolor = config.SURFACE
        _sh = ui_theme.soft_elevation_shadow()
        self._result_card_overlay.shadow = _sh
        self._future_result_card_overlay.shadow = _sh
        self._result_card_overlay.bgcolor = ui_theme.result_card_bg()
        self._result_card_overlay.border = ft.border.all(1, ui_theme.result_card_border())
        self._future_result_card_overlay.bgcolor = ui_theme.result_card_bg()
        self._future_result_card_overlay.border = ft.border.all(1, ui_theme.result_card_border())
        if _ctrl_on_page(self._result_card_overlay):
            self._result_card_overlay.update()
        if _ctrl_on_page(self._future_result_card_overlay):
            self._future_result_card_overlay.update()
        if getattr(self, "_analyse_buttons", None):
            self._refresh_analyse_button_state()
        if self._header_shell:
            self._header_shell.bgcolor = config.SURFACE_VARIANT
        self._rebuild_header_menu_bar()
        self.page.theme = ft.Theme(color_scheme=ui_theme.page_color_scheme())
        _hs = ft.TextStyle(color=config.ON_SURFACE_VARIANT)
        self.editor.hint_style = _hs
        self.tree_search_field.color = config.ON_SURFACE
        self.tree_search_field.hint_style = _hs
        self._chat_input.color = config.ON_SURFACE
        self._chat_input.hint_style = _hs
        self._chat_input.fill_color = config.SURFACE
        self._chat_input.bgcolor = config.SURFACE
        self._chat_input.focused_bgcolor = config.SURFACE
        self._chat_input.border_color = ui_theme.outline_muted()
        self._tree_search_bar.bgcolor = config.SIDEBAR_SURFACE
        self._sync_rag_search_ui()
        self._sync_content_find_replace_field_theme(_hs)
        self._apply_main_workspace_tab_chrome_theme()
        self._apply_ki_tier_tab_bar_theme()
        self.left_panel.content = self._build_left_column()
        self.right_panel.content = self._build_right_column()
        if _ctrl_on_page(self.editor):
            self.editor.update()
            self._compare_editor.update()
            self.dirty_dot.update()
        if _ctrl_on_page(self.filename_text):
            self.filename_text.update()
        if _ctrl_on_page(self.app_symbol):
            self.app_symbol.update()
        if self._main_tab_index == TAB_HISTORY and _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()
        if self._main_tab_index == TAB_HISTORY and _ctrl_on_page(self._compare_newer_dropdown):
            self._compare_newer_dropdown.update()
        if self._main_tab_index == TAB_HISTORY:
            pc = self._plan_compare
            for c in (
                pc.baseline_dd,
                pc.candidate_dd,
                pc.baseline_wrap,
                pc.candidate_wrap,
                pc.baseline_label,
                pc.candidate_label,
            ):
                if _ctrl_on_page(c):
                    c.update()
            self._apply_plan_compare_dropdown_chrome()
        if self._main_tab_index == TAB_FUTURE and _ctrl_on_page(self._review_baseline_dropdown):
            self._review_baseline_dropdown.update()
        if self._main_tab_index == TAB_FUTURE and _ctrl_on_page(self._review_candidate_dropdown):
            self._review_candidate_dropdown.update()
        if _ctrl_on_page(self.left_panel):
            self.left_panel.update()
        if _ctrl_on_page(self.right_panel):
            self.right_panel.update()
        if _ctrl_on_page(self.center_panel):
            self.center_panel.update()
        if _ctrl_on_page(self.tree_search_field):
            self.tree_search_field.update()
        if _ctrl_on_page(self._tree_search_bar):
            self._tree_search_bar.update()
        if _ctrl_on_page(self._chat_input):
            self._chat_input.update()
        if self._header_shell and _ctrl_on_page(self._header_shell):
            self._header_shell.update()
        if getattr(self, "_focus_view_mode", "wysiwyg") == "wysiwyg" and hasattr(
            self, "_sync_wysiwyg_from_editor"
        ):
            self._sync_wysiwyg_from_editor()
        self._refresh_compare_tab_candidate_ui()
        self._refresh_compose_tab_label()
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

    def _next_template_note_path(self) -> Path:
        template = (config.NEW_NOTE_NAME_TEMPLATE or "").strip()
        if template.count("{n}") != 1:
            raise ValueError('new_note_name_template must contain exactly one "{n}" placeholder.')
        root = config.DOCUMENTS
        root.mkdir(parents=True, exist_ok=True)
        left, right = template.split("{n}", 1)
        pattern = re.compile("^" + re.escape(left) + r"(\d+)" + re.escape(right) + r"$")
        max_n = 0
        for p in root.glob("*.md"):
            m = pattern.match(p.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
        return root / template.replace("{n}", str(max_n + 1))

    async def _startup_open_default_note(self) -> None:
        if self.current_path:
            return
        if config.STARTUP_DAILY_LOG:
            await self._startup_open_daily_log()
        else:
            await self._startup_open_first_or_template()

    async def _startup_open_daily_log(self) -> None:
        root = config.DOCUMENTS
        root.mkdir(parents=True, exist_ok=True)
        stamp = date.today().strftime("%Y%m%d")
        dated: list[tuple[int, Path]] = []
        for p in root.glob(f"{stamp}-*.md"):
            rest = p.stem[len(stamp) :]
            if not rest.startswith("-"):
                continue
            suf = rest[1:]
            if suf.isdigit():
                dated.append((int(suf), p))
        if dated:
            path = min(dated, key=lambda x: x[0])[1]
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            await self.open_file(path)
            return
        path = root / f"{stamp}-1.md"
        try:
            path.write_text("", encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not create file: {ex}")
            return
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()
        await self.open_file(path)

    async def _startup_open_first_or_template(self) -> None:
        root = config.DOCUMENTS
        root.mkdir(parents=True, exist_ok=True)
        mode = getattr(self, "_tree_sort_mode", "name_az")
        first = first_markdown_in_tree(root, mode)
        if first is not None:
            await self.open_file(first)
            return
        try:
            path = self._next_template_note_path()
        except ValueError as ex:
            self._snack(str(ex))
            return
        try:
            path.write_text("", encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not create file: {ex}")
            return
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()
        await self.open_file(path)

    async def new_file(self, _e: ft.ControlEvent | None = None) -> None:
        config.DOCUMENTS.mkdir(parents=True, exist_ok=True)
        try:
            path = self._next_dated_note_path() if config.STARTUP_DAILY_LOG else self._next_template_note_path()
        except ValueError as ex:
            self._snack(str(ex))
            return
        try:
            path.write_text("", encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not create file: {ex}")
            return
        self._rebuild_tree_ui()
        self.tree_column.update()
        await self.open_file(path)

    def _ki_comments_for_current_version(self) -> dict[int, str]:
        if not self.current_path:
            return {}
        try:
            ki_ctx = (
                self._active_plan_ki_context()
                if hasattr(self, "_active_plan_ki_context")
                else None
            )
            if ki_ctx is not None:
                from iterthink.persistence import plan_pdf_annotations

                _doc_id, vid = ki_ctx
                with session_scope() as s:
                    return plan_pdf_annotations.plan_comments_map_for_ki(
                        s, content_version_id=int(vid)
                    )
            with session_scope() as s:
                doc = content_repo.get_document_by_resolved_path(s, self.current_path.resolve())
                if doc is None:
                    return {}
                snaps = content_repo.list_snapshots(s, self.current_path.resolve())
                if not snaps:
                    return {}
                anchor_body = content_repo.load_version_body(s, int(snaps[0].version_id))
                display_body = self._editor_buffer() or ""
                return paragraph_user_comments.map_resolved_for_display(
                    s,
                    content_version_id=int(snaps[0].version_id),
                    anchor_body=anchor_body,
                    display_body=display_body,
                )
        except BaseException:
            return {}

    def _ki_comments_use_plan_labels(self) -> bool:
        if hasattr(self, "_active_plan_ki_context"):
            return self._active_plan_ki_context() is not None
        return hasattr(self, "_compose_plan_viewer_active") and self._compose_plan_viewer_active()

    def _ki_plan_comment_meta(self, paragraph_index: int) -> tuple[int, str] | None:
        """``(plan_page_index, annotation_kind)`` for a plan comment slot."""
        from iterthink.persistence import plan_pdf_annotations

        ki_ctx = (
            self._active_plan_ki_context()
            if hasattr(self, "_active_plan_ki_context")
            else None
        )
        if ki_ctx is None:
            return None
        _doc_id, vid = ki_ctx
        with session_scope() as s:
            ann = plan_pdf_annotations.get_by_paragraph_index(
                s,
                content_version_id=int(vid),
                paragraph_index=int(paragraph_index),
            )
        if ann is None:
            return None
        return int(ann.plan_page_index), str(ann.annotation_kind)

    def _ki_plan_comment_rows_for_list(self) -> list[tuple[int, str]]:
        from iterthink.persistence import plan_pdf_annotations

        ki_ctx = (
            self._active_plan_ki_context()
            if hasattr(self, "_active_plan_ki_context")
            else None
        )
        if ki_ctx is None:
            return []
        _doc_id, vid = ki_ctx
        with session_scope() as s:
            anns = plan_pdf_annotations.list_for_plan_version(
                s, content_version_id=int(vid)
            )
        rows: list[tuple[int, str]] = []
        for a in anns:
            body = (a.body or "").strip()
            if a.annotation_kind == plan_pdf_annotations.KIND_CHANGE_REGION:
                rows.append((int(a.paragraph_index), body))
            elif a.annotation_kind == plan_pdf_annotations.KIND_PIN or body:
                rows.append((int(a.paragraph_index), body))
        rows.sort(
            key=lambda r: (
                next(
                    (
                        int(a.plan_page_index)
                        for a in anns
                        if int(a.paragraph_index) == int(r[0])
                    ),
                    0,
                ),
                int(r[0]),
            )
        )
        return rows

    def _ki_plan_comment_raw_body(self, paragraph_index: int) -> str:
        from iterthink.persistence import plan_pdf_annotations

        ki_ctx = (
            self._active_plan_ki_context()
            if hasattr(self, "_active_plan_ki_context")
            else None
        )
        if ki_ctx is None:
            return ""
        _doc_id, vid = ki_ctx
        with session_scope() as s:
            ann = plan_pdf_annotations.get_by_paragraph_index(
                s,
                content_version_id=int(vid),
                paragraph_index=int(paragraph_index),
            )
        return (ann.body or "").strip() if ann is not None else ""

    def _sync_ki_comments_detail_visibility(self) -> None:
        show = self._comment_para_index is not None
        detail = getattr(self, "_ki_comments_detail", None)
        if detail is not None and detail.visible != show:
            detail.visible = show
            if _ctrl_on_page(detail):
                detail.update()

    def _ki_comment_plan_viewer(self):
        if hasattr(self, "_compose_plan_viewer_active") and self._compose_plan_viewer_active():
            return getattr(self, "_compose_plan_focus_viewer", None)
        if hasattr(self, "_review_plan_comment_viewer"):
            viewer = self._review_plan_comment_viewer()
            if viewer is not None:
                return viewer
        return getattr(self, "_compose_plan_focus_viewer", None)

    def _sync_ki_comment_pick_affordance(self) -> None:
        """Review markdown: click pointer + non-selectable rows while pick mode is active."""
        active = bool(getattr(self, "_ki_comment_pick_mode", False))
        on_review_md = (
            int(getattr(self, "_main_tab_index", -1)) == TAB_FUTURE
            and not (
                hasattr(self, "_ki_comments_use_plan_labels")
                and self._ki_comments_use_plan_labels()
            )
        )
        if not on_review_md:
            return

        texts: list[ft.Text] = getattr(self, "_future_left_diff_texts", None) or []
        saved_sel: dict[int, bool] = getattr(self, "_ki_comment_pick_saved_selectable", {})

        for text in texts:
            key = id(text)
            if active:
                if key not in saved_sel:
                    saved_sel[key] = bool(getattr(text, "selectable", True))
                text.selectable = False
            elif key in saved_sel:
                text.selectable = saved_sel.pop(key)
            if _ctrl_on_page(text):
                text.update()

        if not active:
            self._ki_comment_pick_saved_selectable = saved_sel
        else:
            self._ki_comment_pick_saved_selectable = saved_sel

        saved_ro: dict[int, bool] = getattr(self, "_ki_comment_pick_saved_read_only", {})
        fields: list[ft.TextField] = getattr(self, "_compare_right_fields", None) or []
        for field in fields:
            key = id(field)
            if active:
                if key not in saved_ro:
                    saved_ro[key] = bool(getattr(field, "read_only", False))
                field.read_only = True
            elif key in saved_ro:
                field.read_only = saved_ro.pop(key)
            if _ctrl_on_page(field):
                field.update()

        if not active:
            self._ki_comment_pick_saved_read_only = saved_ro
        else:
            self._ki_comment_pick_saved_read_only = saved_ro

        cursor = ft.MouseCursor.CLICK if active else ft.MouseCursor.BASIC
        for cell in getattr(self, "_future_comment_pick_cells", None) or []:
            if getattr(cell, "mouse_cursor", None) != cursor:
                cell.mouse_cursor = cursor
                if _ctrl_on_page(cell):
                    cell.update()

    def _sync_ki_comment_add_btn(self) -> None:
        btn = getattr(self, "_ki_comment_add_btn", None)
        if btn is None:
            return
        active = bool(getattr(self, "_ki_comment_pick_mode", False))
        btn.icon_color = config.PRIMARY_COLOR if active else config.ON_SURFACE_VARIANT
        if _ctrl_on_page(btn):
            btn.update()

    def _sync_plan_review_comment_pick_btn(self) -> None:
        btn = getattr(self, "_plan_review_comment_btn", None)
        if btn is None:
            return
        active = bool(getattr(self, "_ki_comment_pick_mode", False))
        btn.icon_color = config.PRIMARY_COLOR if active else config.ON_SURFACE_VARIANT
        if _ctrl_on_page(btn):
            btn.update()

    def _set_ki_comment_pick_mode(self, active: bool) -> None:
        active = bool(active)
        if active == bool(getattr(self, "_ki_comment_pick_mode", False)):
            if active and self._ki_comments_use_plan_labels():
                viewer = self._ki_comment_plan_viewer()
                if viewer is not None and viewer.interaction_mode != "place_comment":
                    viewer.set_interaction_mode("place_comment")
                    self._sync_plan_review_comment_pick_btn()
                    self._sync_ki_comment_add_btn()
            return
        self._ki_comment_pick_mode = active
        self._sync_ki_comment_add_btn()
        self._sync_ki_comment_pick_affordance()
        if self._ki_comments_use_plan_labels():
            viewer = self._ki_comment_plan_viewer()
            if viewer is not None:
                viewer.set_interaction_mode("place_comment" if active else "idle")
            self._sync_plan_review_comment_pick_btn()

    def _on_ki_comment_add_click(self, _e: ft.ControlEvent | None = None) -> None:
        if getattr(self, "_ki_comment_pick_mode", False):
            self._set_ki_comment_pick_mode(False)
            return
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if not self.right_open:
            self.toggle_right()
        self._set_ki_topic(KI_TOPIC_COMMENTS)
        if self._ki_comments_use_plan_labels():
            if self._ki_comment_plan_viewer() is None:
                self._snack("Open the plan view to place a comment.")
                return
            self._set_ki_comment_pick_mode(True)
            self._snack("Click the plan to place a comment.")
        else:
            self._set_ki_comment_pick_mode(True)
            self._snack("Click a paragraph to comment.")

    async def _on_ki_comment_pick_text_paragraph_async(self, paragraph_index: int) -> None:
        if not getattr(self, "_ki_comment_pick_mode", False):
            return
        self._set_ki_comment_pick_mode(False)
        await self._open_ki_comments_for_paragraph_async(int(paragraph_index), True)

    def _rebuild_ki_comments_list(self) -> None:
        lv = getattr(self, "_ki_comments_list", None)
        if lv is None:
            return
        selected = self._comment_para_index
        if self._ki_comments_use_plan_labels():
            rows = self._ki_plan_comment_rows_for_list()
        else:
            rows = sorted_comment_rows(self._ki_comments_for_current_version())
        controls: list[ft.Control] = []
        if not rows:
            controls.append(
                ft.Text(
                    "No comments in this note yet.",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                    italic=True,
                )
            )
        else:
            plan_labels = self._ki_comments_use_plan_labels()
            for pi, body in rows:
                highlight = selected is not None and int(pi) == int(selected)
                if plan_labels:
                    meta = self._ki_plan_comment_meta(int(pi))
                    title = (
                        plan_comment_list_label(meta[0], meta[1])
                        if meta is not None
                        else paragraph_comment_label(pi)
                    )
                else:
                    title = paragraph_comment_label(pi)
                card = ft.Container(
                    key=f"ki_comment_{pi}",
                    content=ft.Column(
                        [
                            ft.Text(
                                title,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=config.ON_SURFACE,
                            ),
                            ft.Text(
                                body or "(no comment text)",
                                size=12,
                                selectable=True,
                                color=config.ON_SURFACE,
                                italic=not bool(body),
                            ),
                        ],
                        tight=True,
                        spacing=4,
                    ),
                    padding=ft.padding.symmetric(horizontal=8, vertical=6),
                    border_radius=8,
                    bgcolor=(
                        ft.Colors.with_opacity(0.12, config.HIGHLIGHT)
                        if highlight
                        else None
                    ),
                    border=(
                        ft.border.all(1, config.HIGHLIGHT)
                        if highlight
                        else ft.border.all(1, ui_theme.outline_muted(alpha=0.25))
                    ),
                    on_click=lambda _e, p=int(pi): self.page.run_task(
                        self._open_ki_comments_for_paragraph_async, p, False
                    ),
                )
                controls.append(card)
        lv.controls = controls
        if _ctrl_on_page(lv):
            lv.update()

    def _sync_ki_comments_tab_layout(self) -> None:
        comments_active = int(getattr(self, "_ki_topic_index", -1)) == KI_TOPIC_COMMENTS
        chat = getattr(self, "_right_chat_section", None)
        if chat is not None and chat.visible == comments_active:
            chat.visible = not comments_active
            if _ctrl_on_page(chat):
                chat.update()
        for ctrl in (getattr(self, "_ki_topic_tabs", None), getattr(self, "_ki_tab_bar_view", None)):
            if ctrl is not None and bool(getattr(ctrl, "expand", False)) != comments_active:
                ctrl.expand = comments_active
                if _ctrl_on_page(ctrl):
                    ctrl.update()
        if comments_active:
            self._rebuild_ki_comments_list()
            self._sync_ki_comments_detail_visibility()
            self._sync_ki_comment_add_btn()

    def _on_ki_topic_index_changed(self, ix: int) -> None:
        if ix != KI_TOPIC_COMMENTS and getattr(self, "_ki_comment_pick_mode", False):
            self._set_ki_comment_pick_mode(False)
        self._sync_ki_comments_tab_layout()
        if ix == KI_TOPIC_COMMENTS:
            return
        if not getattr(self, "_comment_edit_mode", False):
            return
        self._comment_edit_mode = False
        self._comment_body_edit.visible = False
        self._comment_body_display.visible = True
        self._comment_save_btn.visible = False
        self._comment_cancel_btn.visible = False
        self._comment_edit_btn.visible = bool((self._comment_body_display.value or "").strip())
        for c in (
            self._comment_body_edit,
            self._comment_body_display,
            self._comment_edit_btn,
            self._comment_save_btn,
            self._comment_cancel_btn,
        ):
            if _ctrl_on_page(c):
                c.update()

    def _sync_ki_comment_tab_from_store(self) -> None:
        """Load comment body for ``_comment_para_index`` from the latest snapshot."""
        pi = self._comment_para_index
        if pi is not None and self._ki_comments_use_plan_labels():
            meta = self._ki_plan_comment_meta(int(pi))
            self._comment_heading.value = (
                plan_comment_list_label(meta[0], meta[1])
                if meta is not None
                else paragraph_comment_label(pi)
            )
            body = self._ki_plan_comment_raw_body(int(pi))
        else:
            self._comment_heading.value = (
                paragraph_comment_label(pi) if pi is not None else "No paragraph selected"
            )
            body = ""
            if pi is not None:
                body = self._ki_comments_for_current_version().get(int(pi), "")
        self._comment_body_display.value = body
        if not self._comment_edit_mode:
            self._comment_body_edit.value = body

    async def _scroll_ki_comments_to_paragraph(self, paragraph_index: int) -> None:
        lv = getattr(self, "_ki_comments_list", None)
        if lv is None or not _ctrl_on_page(lv):
            return
        rows = sorted_comment_rows(self._ki_comments_for_current_version())
        idx = next((i for i, (p, _) in enumerate(rows) if int(p) == int(paragraph_index)), None)
        if idx is None:
            return
        key = f"ki_comment_{paragraph_index}"
        try:
            await lv.scroll_to(scroll_key=key, duration=150)
            return
        except (TypeError, AttributeError):
            pass
        await lv.scroll_to(offset=float(idx) * 72.0, duration=150)

    async def _open_ki_comments_for_paragraph_async(
        self, paragraph_index: int | None, start_in_edit: bool = True
    ) -> None:
        if paragraph_index is None:
            self._snack("No paragraph slot for a comment here.")
            return
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if not self.right_open:
            self.toggle_right()
        self._comment_para_index = int(paragraph_index)
        self._comment_edit_mode = bool(start_in_edit)
        self._set_ki_topic(KI_TOPIC_COMMENTS)
        self._ki_comments_detail.visible = True
        self._sync_ki_comment_tab_from_store()
        if start_in_edit:
            self._comment_body_edit.value = self._comment_body_display.value or ""
            self._comment_body_edit.visible = True
            self._comment_body_display.visible = False
            self._comment_edit_btn.visible = False
            self._comment_save_btn.visible = True
            self._comment_cancel_btn.visible = True
        else:
            self._comment_body_edit.visible = False
            self._comment_body_display.visible = True
            self._comment_edit_btn.visible = bool((self._comment_body_display.value or "").strip())
            self._comment_save_btn.visible = False
            self._comment_cancel_btn.visible = False
        self._rebuild_ki_comments_list()
        await self._scroll_ki_comments_to_paragraph(int(paragraph_index))
        if self._ki_comments_use_plan_labels() and hasattr(
            self, "_focus_review_plan_region"
        ):
            self._focus_review_plan_region(int(paragraph_index))
        for c in (
            self._ki_comments_detail,
            self._comment_heading,
            self._comment_body_display,
            self._comment_body_edit,
            self._comment_edit_btn,
            self._comment_save_btn,
            self._comment_cancel_btn,
        ):
            if _ctrl_on_page(c):
                c.update()

    async def _save_ki_paragraph_comment_async(self, _e: ft.ControlEvent | None = None) -> None:
        if self._comment_para_index is None or not self.current_path:
            self._snack("Nothing to save.")
            return
        raw = (self._comment_body_edit.value or "").strip()
        try:
            if self._ki_comments_use_plan_labels():
                from iterthink.persistence import plan_pdf_annotations

                ki_ctx = (
                    self._active_plan_ki_context()
                    if hasattr(self, "_active_plan_ki_context")
                    else None
                )
                if ki_ctx is None:
                    self._snack("Plan PDF is not loaded.")
                    return
                _doc_id, vid = ki_ctx
                with session_scope() as s:
                    ann = plan_pdf_annotations.get_by_paragraph_index(
                        s,
                        content_version_id=int(vid),
                        paragraph_index=int(self._comment_para_index),
                    )
                    if ann is None:
                        self._snack("Plan comment not found.")
                        return
                    plan_pdf_annotations.update_body(s, annotation_id=int(ann.id), body=raw)
            else:
                with session_scope() as s:
                    doc = content_repo.get_document_by_resolved_path(s, self.current_path.resolve())
                    if doc is None:
                        self._snack("Document is not indexed yet.")
                        return
                    snaps = content_repo.list_snapshots(s, self.current_path.resolve())
                    if not snaps:
                        self._snack("Save the note once so a version exists for comments.")
                        return
                    display_body = self._editor_buffer() or ""
                    paragraph_user_comments.upsert(
                        s,
                        content_version_id=int(snaps[0].version_id),
                        paragraph_index=int(self._comment_para_index),
                        body=raw,
                        paragraph_body=display_body,
                    )
        except BaseException as ex:
            self._snack(f"Could not save comment: {ex}")
            return
        if hasattr(self, "_refresh_compose_plan_annotations_overlay"):
            self._refresh_compose_plan_annotations_overlay()
        if hasattr(self, "_refresh_review_plan_annotations_overlay"):
            self._refresh_review_plan_annotations_overlay()
        if hasattr(self, "_reload_review_change_regions_from_db_async"):
            pg = getattr(self, "page", None)
            if pg is not None:
                pg.run_task(self._reload_review_change_regions_from_db_async)
        self._comment_edit_mode = False
        self._sync_ki_comment_tab_from_store()
        self._rebuild_ki_comments_list()
        self._comment_body_edit.visible = False
        self._comment_body_display.visible = True
        self._comment_edit_btn.visible = bool((self._comment_body_display.value or "").strip())
        self._comment_save_btn.visible = False
        self._comment_cancel_btn.visible = False
        for c in (
            self._comment_body_edit,
            self._comment_body_display,
            self._comment_edit_btn,
            self._comment_save_btn,
            self._comment_cancel_btn,
        ):
            if _ctrl_on_page(c):
                c.update()
        self._refresh_compare_diff_immediate()

    async def save_file(
        self,
        _e: ft.ControlEvent | None = None,
        *,
        silent: bool = False,
        snapshot_reason: content_repo.SnapshotReason | None = None,
        version_display_label: str | None = None,
        persist_snapshot: bool = True,
        for_shutdown: bool = False,
    ) -> None:
        if not self.current_path:
            if not silent:
                self._snack("Open or create a note first. Whatever you want to find")
            return
        self._flush_review_edits_if_changed(refresh_compare_ui=not for_shutdown)
        buf = self._working_document_text()
        reason: content_repo.SnapshotReason = snapshot_reason or ("autosave" if silent else "manual")
        try:
            self.current_path.write_text(buf, encoding="utf-8")
        except OSError as ex:
            self._snack(f"Save failed: {ex}")
            return
        self.last_saved_text = buf
        try:
            with session_scope() as s:
                content_repo.update_document_last_disk_state(s, self.current_path.resolve(), body=buf)
        except BaseException:
            pass
        if persist_snapshot:
            try:
                with session_scope() as s:
                    if version_display_label:
                        content_repo.persist_version_snapshot(
                            s,
                            self.current_path.resolve(),
                            buf,
                            "ai_apply",
                            display_label=version_display_label,
                        )
                    else:
                        content_repo.persist_version_snapshot(s, self.current_path.resolve(), buf, reason)
            except BaseException:
                pass
            self.schedule_rag_reindex(self.current_path.resolve())
        if not for_shutdown:
            self._refresh_compare_tab_candidate_ui()
            self._margin_gen += 1
            if self._main_tab_index == TAB_PRESENT:
                self.page.run_task(self._debounced_compose_rebuild, self._margin_gen)
            else:
                self._refresh_compare_diff_immediate()
            self._refresh_title_bar()
        if not silent:
            self._snack("Saved.")

    def _export_paragraph_comments_for_doc(
        self, md_path: Path, *, content_version_id: int | None = None
    ) -> dict[int, str]:
        try:
            with session_scope() as s:
                doc = content_repo.get_document_by_resolved_path(s, md_path)
                if doc is None:
                    return {}
                vid = content_version_id
                if vid is None:
                    snaps = content_repo.list_snapshots(s, md_path)
                    if not snaps:
                        return {}
                    vid = snaps[0].version_id
                return impact_annotations.paragraph_comments_map_for_export(
                    s, content_version_id=int(vid)
                )
        except BaseException:
            return {}

    def _resolve_export_markdown(
        self,
        md_path: Path,
        version_key: str | None,
        *,
        is_current: bool,
    ) -> tuple[str, int | None]:
        """Return (markdown body, content_version_id for comments)."""
        if version_key == _COMPARE_KEY_CURRENT:
            return self.editor.value or "", None
        if version_key:
            try:
                vid = int(version_key)
            except (TypeError, ValueError):
                vid = None
            if vid is not None:
                with session_scope() as s:
                    return content_repo.load_version_body(s, vid), vid
        if is_current:
            return self.editor.value or "", None
        try:
            return md_path.read_text(encoding="utf-8"), None
        except OSError as ex:
            raise OSError(f"Could not read file: {ex}") from ex

    async def _complete_export_to_word_async(
        self,
        *,
        md_path: Path,
        markdown_src: str,
        template_path: Path,
        author: str,
        content_version_id: int | None = None,
    ) -> None:
        """Pick save path and write DOCX (call after the template dialog is closed)."""
        self.ensure_file_pickers()
        if _ctrl_on_page(self.page):
            self.page.update()
        await asyncio.sleep(_EXPORT_SAVE_DIALOG_DELAY_SEC)

        initial_dir: str | None = None
        if md_path.parent.is_dir():
            initial_dir = str(md_path.parent)
        elif config.DOCUMENTS.is_dir():
            initial_dir = str(config.DOCUMENTS)

        default_name = f"{md_path.stem}.docx"
        try:
            dest = await self._fp_export_docx.save_file(
                dialog_title="Export Word document",
                file_name=default_name,
                initial_directory=initial_dir,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["docx"],
            )
        except BaseException as ex:
            self._snack(f"Save dialog failed: {ex}")
            return
        if not dest:
            self._snack("Export cancelled.")
            return

        try:
            out = normalize_save_file_path(
                dest, default_file_name=default_name, expected_suffix=".docx"
            )
        except ValueError:
            self._snack("Export cancelled.")
            return

        meta = markdown_docx_export.ExportMeta(
            title_stem=md_path.stem,
            author=author,
            date_iso=date.today().isoformat(),
            comment_author=author,
        )
        para_comments = self._export_paragraph_comments_for_doc(
            md_path, content_version_id=content_version_id
        )

        def _run() -> None:
            markdown_docx_export.markdown_to_docx(
                markdown_src=markdown_src,
                md_path=md_path,
                template_path=template_path,
                output_path=out,
                meta=meta,
                paragraph_comments=para_comments or None,
            )

        try:
            await asyncio.to_thread(_run)
        except BaseException as ex:
            self._snack(f"Export failed: {ex}")
            return
        if not out.is_file() or out.stat().st_size == 0:
            self._snack("Export failed: output file was not written.")
            return
        self._snack(f"Exported to {out.name}")

    async def begin_export_to_word(self, tree_path: Path | None = None) -> None:
        self.ensure_file_pickers()
        templates = markdown_docx_export.list_docx_templates()
        if not templates:
            self._snack(
                "No Word templates found. Add .docx under the app templates folder or "
                f"{markdown_docx_export.user_templates_dir()}."
            )
            return

        if tree_path is None:
            if not self.current_path:
                self._snack("Open a markdown file first.")
                return
            md_path = self.current_path.resolve()
        else:
            md_path = tree_path.resolve()

        cur = self.current_path.resolve() if self.current_path else None
        is_current = cur is not None and md_path == cur

        snaps_all: list[content_repo.SnapshotInfo] = []
        with session_scope() as s:
            snaps_all = content_repo.list_snapshots(s, md_path)
        filt = history_compare_snapshots(snaps_all)
        dd_style = ui_theme.compare_candidate_dropdown_option_style()
        version_opts: list[ft.dropdown.Option] = []
        if is_current and self._is_dirty():
            version_opts.append(
                ft.dropdown.Option(key=_COMPARE_KEY_CURRENT, text="Current draft", style=dd_style)
            )
        version_opts.extend(build_history_snapshot_dropdown_options(filt, dd_style))

        version_dd: ft.Dropdown | None = None
        if version_opts:
            default_version_key: str | None = None
            if is_current and self._is_dirty():
                default_version_key = _COMPARE_KEY_CURRENT
            else:
                for opt in version_opts:
                    if opt.key != _COMPARE_KEY_CURRENT:
                        default_version_key = opt.key
                        break
            version_dd = ft.Dropdown(
                label="Version",
                options=version_opts,
                value=default_version_key or version_opts[0].key,
                dense=True,
            )

        author = store_db.settings_get(self._db, store_db.SETTINGS_EXPORT_AUTHOR) or ""
        paths_by_label = {label: path for label, path in templates}
        labels = [lbl for lbl, _ in templates]

        tpl_dd = ft.Dropdown(
            label="Template",
            options=[ft.dropdown.Option(l) for l in labels],
            value=labels[0],
            dense=True,
        )

        async def on_export(_e: ft.ControlEvent | None = None) -> None:
            try:
                sel = (tpl_dd.value or "").strip()
                tpl = paths_by_label.get(sel)
                if tpl is None or not tpl.is_file():
                    self._snack("Choose a template.")
                    return
                version_key = version_dd.value if version_dd is not None else None
                try:
                    markdown_src, content_version_id = self._resolve_export_markdown(
                        md_path,
                        version_key,
                        is_current=is_current,
                    )
                except OSError as ex:
                    self._snack(str(ex))
                    return
                self.page.pop_dialog()
                await self._complete_export_to_word_async(
                    md_path=md_path,
                    markdown_src=markdown_src,
                    template_path=tpl,
                    author=author,
                    content_version_id=content_version_id,
                )
            except BaseException as ex:
                self._snack(f"Export failed: {ex}")

        # Yield so the File menu / Material menu overlay can finish closing before we
        # stack a modal dialog; opening both in the same frame often shows nothing (Flet desktop).
        await asyncio.sleep(0)
        try:
            self.page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Export", weight=ft.FontWeight.W_600),
                    content=ft.Container(
                        width=560,
                        padding=ft.padding.only(top=8),
                        content=ft.Column(
                            [
                                ft.Text(
                                    "Export to Word",
                                    size=13,
                                    weight=ft.FontWeight.W_500,
                                    color=config.ON_SURFACE,
                                ),
                                ft.Text(md_path.name, size=12, font_family="monospace"),
                                *([version_dd] if version_dd is not None else []),
                                tpl_dd,
                            ],
                            tight=True,
                            spacing=8,
                        ),
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                        ft.TextButton("Export", on_click=lambda _e: self.page.run_task(on_export)),
                    ],
                    actions_alignment=ft.MainAxisAlignment.END,
                )
            )
        except BaseException as ex:
            self._snack(f"Export dialog failed: {ex}")

    async def _periodic_file_drift_loop(self) -> None:
        while True:
            await asyncio.sleep(90.0)
            try:
                await self._check_file_drift_async()
            except asyncio.CancelledError:
                break
            except BaseException:
                pass

    async def _check_file_drift_async(self) -> None:
        if self.page.web or self._file_drift_dialog_open:
            return
        path = self.current_path
        if not path or not path.is_file():
            return
        try:
            with session_scope() as s:
                stale = content_repo.is_document_disk_stale(s, path)
        except BaseException:
            return
        if not stale:
            return
        if not self._is_dirty():
            try:
                await self.open_file(path)
            except BaseException:
                pass
            self._snack("File changed on disk — reloaded.")
            return

        self._file_drift_dialog_open = True

        async def on_reload(_e: ft.ControlEvent | None) -> None:
            self.page.pop_dialog()
            self._file_drift_dialog_open = False
            c = self.current_path
            if c:
                await self.open_file(c)
            self._snack("Reloaded from disk.")

        async def on_keep(_e: ft.ControlEvent | None) -> None:
            self.page.pop_dialog()
            self._file_drift_dialog_open = False
            c = self.current_path
            if c:
                try:
                    with session_scope() as s:
                        content_repo.refresh_document_last_disk_state_from_disk(s, c)
                except BaseException:
                    pass
            self._snack("Keeping your edits. Saving will overwrite the file on disk.")

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("File changed on disk", weight=ft.FontWeight.W_600),
                content=ft.Text(
                    f'"{path.name}" was modified outside this app. Reload and discard your unsaved changes, '
                    "or keep editing (your next save will overwrite the file on disk).",
                    size=13,
                ),
                actions=[
                    ft.TextButton("Keep editing", on_click=lambda e: self.page.run_task(on_keep(e))),
                    ft.TextButton("Reload from disk", on_click=lambda e: self.page.run_task(on_reload(e))),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

