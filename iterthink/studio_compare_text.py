
"""Compare tab: candidates, paragraph rows, bulk AI accept."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft
import httpx

from iterthink import config
from iterthink import prompts
from iterthink.compare_layout import pair_paragraphs_for_compare
from iterthink.diff_card import build_unified_spans
from iterthink.db.session import session_scope
from iterthink.paragraph_align import compute_hash
from iterthink import paragraph_compare
from iterthink import version_storage
from iterthink.margin import (
    distribute_heights,
    estimate_total_editor_height,
    paragraph_compose_slot_weights,
    paragraph_index_at_offset,
    replace_paragraph_at_index,
    split_paragraphs,
)
from iterthink.llm_router import remote_http_error_message
from iterthink.ollama_util import chat_response_text, chat_stream_delta, ollama_error_message
from iterthink.prompts import TOPIC_CHANGE
from iterthink.studio_compose import (
    _compare_grid_slot,
    _ki_topic_index_for_prompt_topic,
    _strip_change_topic_preamble,
)
from iterthink.studio_constants import (
    COMPARE_ACTION_COL_W,
    COMPARE_ACTION_GRID_CELL,
    COMPARE_ACTION_H_PAD,
    COMPARE_ACTION_INNER_W,
    COMPARE_ACTION_V_PAD,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_KEY_AI as _COMPARE_KEY_AI,
    COMPARE_KEY_DOCX_ORIGINAL as _COMPARE_KEY_DOCX_ORIGINAL,
    COMPARE_KEY_DRAFT as _COMPARE_KEY_DRAFT,
    COMPARE_KEY_PDF_ORIGINAL as _COMPARE_KEY_PDF_ORIGINAL,
    COMPARE_PILL_COL_W,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    PROJECT_PAGE_TOOLTIP as _PROJECT_PAGE_TOOLTIP,
    PROJECT_PAGE_URL as _PROJECT_PAGE_URL,
)
from iterthink.studio_util import ctrl_on_page as _ctrl_on_page


class MarkdownStudioCompareText:
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

    def _compare_should_offer_pdf_original(self) -> bool:
        if not self.current_path:
            return False
        with session_scope() as s:
            rp = self.current_path.resolve()
            if self._compare_candidate_source == "pdf_original":
                if self._compare_pdf_peer_snapshot_id is not None:
                    return bool(
                        version_storage.get_version_pdf_relpath(s, self._compare_pdf_peer_snapshot_id)
                    )
                return version_storage.document_has_any_pdf(s, rp)
            if self._compare_candidate_source == "snapshot" and self._compare_snapshot_version_id is not None:
                return bool(
                    version_storage.get_version_pdf_relpath(s, self._compare_snapshot_version_id)
                )
            return version_storage.document_has_any_pdf(s, rp)

    def _compare_should_offer_docx_original(self) -> bool:
        if not self.current_path:
            return False
        with session_scope() as s:
            rp = self.current_path.resolve()
            if self._compare_candidate_source == "docx_original":
                if self._compare_pdf_peer_snapshot_id is not None:
                    return bool(
                        version_storage.get_version_docx_relpath(s, self._compare_pdf_peer_snapshot_id)
                    )
                return version_storage.document_has_any_docx(s, rp)
            if self._compare_candidate_source == "snapshot" and self._compare_snapshot_version_id is not None:
                return bool(
                    version_storage.get_version_docx_relpath(s, self._compare_snapshot_version_id)
                )
            return version_storage.document_has_any_docx(s, rp)

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
        if self._compare_should_offer_pdf_original():
            opts.append(ft.dropdown.Option(key=_COMPARE_KEY_PDF_ORIGINAL, text="Original PDF"))
        if self._compare_should_offer_docx_original():
            opts.append(ft.dropdown.Option(key=_COMPARE_KEY_DOCX_ORIGINAL, text="Original Word"))
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
        elif self._compare_candidate_source == "pdf_original":
            self._compare_candidate_dropdown.value = (
                _COMPARE_KEY_PDF_ORIGINAL if _COMPARE_KEY_PDF_ORIGINAL in keys_ok else _COMPARE_KEY_DRAFT
            )
        elif self._compare_candidate_source == "docx_original":
            self._compare_candidate_dropdown.value = (
                _COMPARE_KEY_DOCX_ORIGINAL if _COMPARE_KEY_DOCX_ORIGINAL in keys_ok else _COMPARE_KEY_DRAFT
            )
        else:
            self._compare_candidate_dropdown.value = _COMPARE_KEY_DRAFT
        if self._compare_candidate_source == "pdf_original" and _COMPARE_KEY_PDF_ORIGINAL not in keys_ok:
            self._compare_candidate_source = "draft"
            self._compare_pdf_peer_snapshot_id = None
            self._compare_candidate_dropdown.value = _COMPARE_KEY_DRAFT
            self._sync_compare_pdf_layers_visibility()
            if self._main_tab_index == 1:
                self._rebuild_compare_paragraph_ui()
        if self._compare_candidate_source == "docx_original" and _COMPARE_KEY_DOCX_ORIGINAL not in keys_ok:
            self._compare_candidate_source = "draft"
            self._compare_pdf_peer_snapshot_id = None
            self._compare_candidate_dropdown.value = _COMPARE_KEY_DRAFT
            self._sync_compare_pdf_layers_visibility()
            if self._main_tab_index == 1:
                self._rebuild_compare_paragraph_ui()
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()
        self._refresh_plan_compare_bar()

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
        self._refresh_compare_bulk_buttons()

    def _compare_has_pending_bulk_apply(self) -> bool:
        if not self.current_path or not self._compare_right_fields:
            return False
        merged = "\n\n".join(tf.value or "" for tf in self._compare_right_fields)
        return merged != (self.editor.value or "")

    def _refresh_compare_bulk_buttons(self) -> None:
        n = len(self._compare_right_fields)
        pending_apply = self._compare_has_pending_bulk_apply()
        hide_bulk = self._compare_candidate_source in ("pdf_original", "docx_original")
        self._compare_approve_all_btn.visible = pending_apply and not hide_bulk
        self._compare_decline_all_btn.disabled = n == 0 or hide_bulk
        if _ctrl_on_page(self._compare_approve_all_btn):
            self._compare_approve_all_btn.update()
        if _ctrl_on_page(self._compare_decline_all_btn):
            self._compare_decline_all_btn.update()

    async def _on_compare_candidate_change_async(self, e: ft.ControlEvent) -> None:
        if self._compare_candidate_dropdown.disabled or not self.current_path:
            return
        v = e.control.value
        if v is None or v == _COMPARE_KEY_DRAFT:
            self._compare_candidate_source = "draft"
            self._compare_snapshot_version_id = None
            self._pending_ai_accept_action_id = None
            self._compare_pdf_peer_snapshot_id = None
            self._compare_editor.value = self.editor.value or ""
            self._capture_compare_baseline_snapshot()
            if _ctrl_on_page(self._compare_editor):
                self._compare_editor.update()
            self._sync_compare_pdf_layers_visibility()
            self._rebuild_compare_paragraph_ui()
        elif v == _COMPARE_KEY_AI:
            return
        elif v == _COMPARE_KEY_PDF_ORIGINAL:
            peer = self._compare_snapshot_version_id if self._compare_candidate_source == "snapshot" else None
            self._compare_pdf_peer_snapshot_id = peer
            self._compare_candidate_source = "pdf_original"
            self._pending_ai_accept_action_id = None
            self._rebuild_compare_pdf_panes()
            self._sync_compare_pdf_layers_visibility()
        elif v == _COMPARE_KEY_DOCX_ORIGINAL:
            peer = self._compare_snapshot_version_id if self._compare_candidate_source == "snapshot" else None
            self._compare_pdf_peer_snapshot_id = peer
            self._compare_candidate_source = "docx_original"
            self._pending_ai_accept_action_id = None
            self._rebuild_compare_docx_panes()
            self._sync_compare_pdf_layers_visibility()
        else:
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            self._compare_candidate_source = "snapshot"
            self._compare_snapshot_version_id = vid
            self._compare_pdf_peer_snapshot_id = None
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
            self._sync_compare_pdf_layers_visibility()
            self._rebuild_compare_paragraph_ui()
        # Dropdown sits on the Compare tab label; user may pick a candidate without clicking the tab first.
        if self._main_tab_index != 1:
            self._main_tabs.selected_index = 1
            if _ctrl_on_page(self._main_tabs):
                self._main_tabs.update()
            await self._sync_tab_switch_async(1)
        self._refresh_compare_diff_immediate()
        self._refresh_compare_bulk_buttons()
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
            if self._compare_candidate_source == "pdf_original":
                self._rebuild_compare_pdf_panes()
                self._sync_compare_pdf_layers_visibility()
            elif self._compare_candidate_source == "docx_original":
                self._rebuild_compare_docx_panes()
                self._sync_compare_pdf_layers_visibility()
            else:
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
        elif new_ix == 1:
            self._refresh_plan_compare_bar()
        self._refresh_title_bar()

    def _compare_para_text_style(self) -> ft.TextStyle:
        return ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
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
        bulk: bool = False,
    ) -> None:
        """Two DB snapshots for AI Compare accept: document before merge, then after (parent chain)."""
        if not self.current_path:
            return
        act = prompts.get_margin_action(action_id)
        apply_label = act.label if act else action_id
        before_label = "Before accept · all paragraphs" if bulk else f"Before accept · paragraph {para_index + 1}"
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
        """Word-level inline diff for the left (Compose) column.

        ``left_para`` / ``right_para`` match ``pair_paragraphs_for_compare``: compose baseline
        vs compare-column snapshot. ``build_unified_spans`` expects (old, new), so we pass
        ``(right_para, left_para)`` — snapshot as old, Compose as new.
        """
        cap = 80_000
        lp, rp = left_para, right_para
        if len(lp) + len(rp) > cap:
            lp = lp[: cap // 2] + "\n…"
            rp = rp[: cap // 2] + "\n…"
        return build_unified_spans(
            rp,
            lp,
            base_size=COMPARE_COL_FONT_SIZE,
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
        self._compare_eval_hosts.clear()
        # Hide any active result-card overlay; row layout is being rebuilt.
        self._result_card_overlay.visible = False
        self._result_card_visible_for = None
        if _ctrl_on_page(self._result_card_overlay):
            self._result_card_overlay.update()
        # Refresh paragraph hash list against the new candidate; resize results buffers.
        self._check_para_hashes = [compute_hash(new) for _, new in pairs]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != len(pairs):
                self._check_results[cid] = (results + [None] * len(pairs))[: len(pairs)]

        para_style = self._compare_para_text_style()
        shared_tf_kwargs: dict[str, Any] = {
            "multiline": True,
            "max_lines": None,
            "min_lines": 1,
            "border": ft.InputBorder.NONE,
            "filled": False,
            "dense": True,
            "text_size": COMPARE_COL_FONT_SIZE,
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
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4),
            )
            eval_cell = self._build_eval_cell(i)
            self._compare_eval_hosts.append(eval_cell)

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
                row_h = COMPARE_ACTION_GRID_CELL
                inner_w = COMPARE_ACTION_INNER_W
                actions_inner = ft.Container(
                    bgcolor=ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
                    border=ft.border.all(1, ft.Colors.with_opacity(0.45, ft.Colors.GREY_600)),
                    border_radius=8,
                    padding=ft.padding.symmetric(
                        horizontal=COMPARE_ACTION_H_PAD,
                        vertical=COMPARE_ACTION_V_PAD,
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
                    width=COMPARE_ACTION_COL_W,
                    right=0,
                    top=4,
                )
                actions_ctrl = actions_hover_wrap
            else:
                actions_ctrl = None

            row_inner = ft.Row(
                [
                    eval_cell,
                    left_cell,
                    pill_host,
                    ft.Container(right_tf, expand=1),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            if actions_ctrl is not None:
                row: ft.Control = ft.Stack(
                    [
                        ft.Container(
                            content=row_inner,
                            expand=True,
                            on_hover=lambda e, w=actions_hover_wrap: self._on_compare_row_hover(e, w),
                        ),
                        actions_ctrl,
                    ],
                )
            else:
                row = row_inner
            self._compare_rows_listview.controls.append(row)
            self._compare_right_fields.append(right_tf)
            self._compare_row_pill_hosts.append(pill_host)

        self._sync_compare_buffer_from_fields()
        if _ctrl_on_page(self._compare_rows_listview):
            self._compare_rows_listview.update()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _refresh_compare_diff_immediate(self) -> None:
        if self._compare_candidate_source == "pdf_original":
            self._rebuild_compare_pdf_panes()
            self._sync_compare_pdf_layers_visibility()
            return
        if self._compare_candidate_source == "docx_original":
            self._rebuild_compare_docx_panes()
            self._sync_compare_pdf_layers_visibility()
            return
        self._rebuild_compare_paragraph_ui()

    async def _debounced_compare_diff(self, gen: int) -> None:
        await asyncio.sleep(0.12)
        if gen != self._compare_diff_gen:
            return
        if self._compare_candidate_source in ("pdf_original", "docx_original"):
            return
        cand = self._compare_editor.value or ""
        n_para = len(split_paragraphs(cand))
        if n_para != len(self._compare_right_fields):
            self._rebuild_compare_paragraph_ui()
            return
        self._refresh_compare_left_diff_spans()
        # Drop in-memory analysis results for paragraphs whose new-text hash changed.
        self._invalidate_check_results_for_changes()
        if self._active_check_id is not None:
            self._refresh_all_eval_cells()
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
                self._make_llm_backend(),
                chat_model=self.chat_model_for_requests(),
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

    async def _compare_approve_all_async(self) -> None:
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if not self._compare_right_fields:
            return
        parts = [tf.value or "" for tf in self._compare_right_fields]
        new_buf = "\n\n".join(parts)
        pre_buf = self.editor.value or ""
        ai_flow = (
            self._compare_candidate_source == "ai_preview"
            and self._pending_ai_accept_action_id
        )
        if ai_flow:
            self._persist_ai_accept_snapshots(
                pre_buf,
                new_buf,
                para_index=0,
                action_id=self._pending_ai_accept_action_id,
                bulk=True,
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
        self._snack("All paragraphs applied to the document.")

    async def _compare_decline_all_async(self) -> None:
        if not self._compare_right_fields:
            return
        baseline = self._compare_latest_baseline_text()
        paras = split_paragraphs(baseline or "")
        for i, tf in enumerate(self._compare_right_fields):
            if i < len(self._compare_row_stable_texts):
                revert = self._compare_row_stable_texts[i]
            else:
                revert = paras[i] if i < len(paras) else ""
            tf.value = revert
            if _ctrl_on_page(tf):
                tf.update()
        self._sync_compare_buffer_from_fields()
        self._rebuild_compare_paragraph_ui()
        self._refresh_title_bar()
        self._snack("All paragraphs reset to match latest.")

    def _hide_prompt_footer(self, footer: ft.Row) -> None:
        footer.controls.clear()
        footer.visible = False
        if _ctrl_on_page(footer):
            footer.update()

    def _compare_paragraph_for_index(self, idx: int) -> str:
        if 0 <= idx < len(self._compare_right_fields):
            return self._compare_right_fields[idx].value or ""
        paras = split_paragraphs(self._compare_editor.value or "")
        return paras[idx] if 0 <= idx < len(paras) else ""

    async def _stage_compare_margin_review_async(
        self, idx: int, reply: ft.Text, footer: ft.Row, action_id: str
    ) -> None:
        """Like _stage_ai_candidate_async but replaces within the Compare candidate buffer (already on Compare)."""
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
        base = self._compare_editor.value or ""
        self._compare_editor.value = replace_paragraph_at_index(base, idx, text)
        self._compare_candidate_source = "ai_preview"
        self._compare_snapshot_version_id = None
        self._pending_ai_accept_action_id = action_id
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_compare_tab_candidate_ui()
        self._compare_candidate_dropdown.value = _COMPARE_KEY_AI
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

    async def _run_compare_margin_action(self, action_id: str, idx: int) -> None:
        """Compare-tab sparkle: run margin prompt on the candidate paragraph; stage result into Compare."""
        act = prompts.get_margin_action(action_id)
        if act is None:
            return
        para = self._compare_paragraph_for_index(idx).strip()
        if not para:
            self._snack("This paragraph is empty.")
            return

        self._set_ki_topic(_ki_topic_index_for_prompt_topic(act.topic))

        self._append_chat_line("user", f"Compare · paragraph {idx + 1}: {act.label}")

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
        backend = self._make_llm_backend()
        cm = self.chat_model_for_requests()
        try:
            stream = await backend.chat(model=cm, messages=messages, stream=True)
            async for part in stream:
                acc += chat_stream_delta(part)
                live = _strip_change_topic_preamble(acc) if act.topic == TOPIC_CHANGE else acc
                reply.value = live.strip() or "…"
                if _ctrl_on_page(reply):
                    reply.update()
        except BaseException:
            try:
                resp = await backend.chat(model=cm, messages=messages, stream=False)
                acc = chat_response_text(resp) or ""
            except BaseException as ex_final:
                if isinstance(ex_final, httpx.HTTPStatusError):
                    detail = remote_http_error_message(ex_final)
                elif isinstance(ex_final, ValueError):
                    detail = str(ex_final)
                else:
                    detail = ollama_error_message(ex_final)
                reply.value = f"(Error) {detail}"
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
                    tooltip="Stage this text as the Compare candidate for this paragraph",
                    on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                        self._stage_compare_margin_review_async, i, r, f, aid
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

