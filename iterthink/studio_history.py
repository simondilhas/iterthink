
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
from iterthink.action_chrome import wrap_workspace_action_chrome
from iterthink.studio_components import (
    ACTION_RAIL_ICON_SIZE,
    action_rail_approve_icon_button,
    action_rail_icon_button_style,
    action_rail_play_icon_button,
    action_rail_reject_icon_button,
    build_action_rectangle,
)
from iterthink.studio_focus_area import (
    _ki_topic_index_for_prompt_topic,
    _strip_change_topic_preamble,
)
from iterthink.studio_constants import (
    COMPARE_ACTION_GRID_CELL,
    COMPARE_CANDIDATE_DROPDOWN_OPTION_STYLE,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_KEY_CANDIDATE as _COMPARE_KEY_CANDIDATE,
    COMPARE_KEY_CURRENT as _COMPARE_KEY_CURRENT,
    COMPARE_PILL_COL_W,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    PROJECT_PAGE_URL as _PROJECT_PAGE_URL,
    TAB_HISTORY,
    TAB_PRESENT,
    TAB_FUTURE,
)
from iterthink.studio_util import ctrl_on_page as _ctrl_on_page


def _blend_hex_rgb(a: str, b: str, t: float) -> str:
    def _p(h: str) -> tuple[int, int, int]:
        h = h.strip().removeprefix("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    r1, g1, b1 = _p(a)
    r2, g2, b2 = _p(b)

    def _m(c1: int, c2: int) -> int:
        return round(c1 + (c2 - c1) * t)

    return f"#{_m(r1, r2):02x}{_m(g1, g2):02x}{_m(b1, b2):02x}"


class MarkdownStudioCompareText:
    def _working_document_text(self) -> str:
        """Text compared to on-disk `last_saved_text` for dirty + save."""
        return self.editor.value or ""

    def _is_dirty(self) -> bool:
        return self._working_document_text() != self.last_saved_text

    def _editor_buffer(self) -> str:
        if self._main_tab_index in (TAB_HISTORY, TAB_FUTURE):
            return self._compare_editor.value or ""
        return self.editor.value or ""

    def _refresh_compare_tab_candidate_ui(self) -> None:
        _st = COMPARE_CANDIDATE_DROPDOWN_OPTION_STYLE

        # ── History dropdown: version snapshots only ──────────────────────────
        history_opts: list[ft.dropdown.Option] = [
            ft.dropdown.Option(key=_COMPARE_KEY_CURRENT, text="Current draft", style=_st)
        ]
        if self.current_path:
            with session_scope() as s:
                snaps = version_storage.list_snapshots(s, self.current_path.resolve())
            snap_opts: list[ft.dropdown.Option] = []
            import_opts: list[ft.dropdown.Option] = []
            for sn in snaps:
                row_text = version_storage.snapshot_dropdown_text(sn)
                bucket = version_storage.snapshot_bucket(sn)
                if bucket == "import":
                    import_opts.append(
                        ft.dropdown.Option(
                            key=str(sn.version_id),
                            text=f"Import - {row_text}",
                            style=_st,
                        )
                    )
                else:
                    snap_opts.append(
                        ft.dropdown.Option(
                            key=str(sn.version_id),
                            text=row_text,
                            style=_st,
                        )
                    )
            history_opts.extend(snap_opts)
            history_opts.extend(import_opts)

        self._compare_candidate_dropdown.options = history_opts
        h_keys = {o.key for o in history_opts}
        if self._compare_candidate_source in ("snapshot", "pdf_original", "docx_original"):
            sk = (
                str(self._compare_snapshot_version_id)
                if self._compare_snapshot_version_id is not None
                else None
            )
            if sk is None and self._compare_pdf_peer_snapshot_id is not None:
                sk = str(self._compare_pdf_peer_snapshot_id)
            self._compare_candidate_dropdown.value = sk if sk in h_keys else _COMPARE_KEY_CURRENT
        else:
            self._compare_candidate_dropdown.value = _COMPARE_KEY_CURRENT
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()

        # ── Review dropdown: AI candidates + imports only ─────────────────────
        review_opts: list[ft.dropdown.Option] = []
        if self._pending_ai_accept_action_id:
            act = prompts.get_margin_action(self._pending_ai_accept_action_id)
            cand_text = act.label if act else "AI Proposal"
            review_opts.append(
                ft.dropdown.Option(key=_COMPARE_KEY_CANDIDATE, text=cand_text, style=_st)
            )
        if self.current_path:
            with session_scope() as s:
                snaps = version_storage.list_snapshots(s, self.current_path.resolve())
            for sn in snaps:
                if version_storage.snapshot_bucket(sn) == "import":
                    review_opts.append(
                        ft.dropdown.Option(
                            key=str(sn.version_id),
                            text=f"Import - {version_storage.snapshot_dropdown_text(sn)}",
                            style=_st,
                        )
                    )
        self._review_candidate_dropdown.options = review_opts
        r_keys = {o.key for o in review_opts}
        if self._compare_candidate_source == "ai_preview" and _COMPARE_KEY_CANDIDATE in r_keys:
            self._review_candidate_dropdown.value = _COMPARE_KEY_CANDIDATE
        elif review_opts:
            self._review_candidate_dropdown.value = review_opts[0].key
        else:
            self._review_candidate_dropdown.value = None
        self._review_candidate_dropdown.disabled = not bool(review_opts)
        if _ctrl_on_page(self._review_candidate_dropdown):
            self._review_candidate_dropdown.update()

        self._refresh_plan_compare_bar()

    def _sync_version_toolbar_state(self) -> None:
        has_doc = self.current_path is not None
        self._compare_candidate_dropdown.disabled = not has_doc
        self._compare_candidate_dropdown.tooltip = (
            "Pick a version snapshot to compare against the current draft."
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
        # Approve/decline bulk buttons only shown on Future tab (ai_preview).
        on_future = self._main_tab_index == TAB_FUTURE
        pending_apply = self._compare_has_pending_bulk_apply() if on_future else False
        self._compare_approve_all_btn.visible = pending_apply and on_future
        self._compare_decline_all_btn.disabled = n == 0 or not on_future
        if _ctrl_on_page(self._compare_approve_all_btn):
            self._compare_approve_all_btn.update()
        if _ctrl_on_page(self._compare_decline_all_btn):
            self._compare_decline_all_btn.update()

    async def _on_compare_candidate_change_async(self, e: ft.ControlEvent) -> None:
        from_review = e.control is self._review_candidate_dropdown
        if not from_review and (self._compare_candidate_dropdown.disabled or not self.current_path):
            return
        v = e.control.value

        if from_review:
            # Review dropdown: AI candidate or import — stays on Review tab.
            if v is None:
                return
            if v == _COMPARE_KEY_CANDIDATE:
                # Already staged in-memory; just rebuild the Review view.
                self._compare_candidate_source = "ai_preview"
                self._rebuild_future_paragraph_ui()
            else:
                try:
                    vid = int(v)
                except (TypeError, ValueError):
                    return
                # Load the import as the Review right column without leaving Review.
                self._select_snapshot_as_candidate(vid)
                self._rebuild_future_paragraph_ui()
            self._refresh_compare_tab_candidate_ui()
            self._refresh_compare_diff_immediate()
            self._refresh_compare_bulk_buttons()
            self._refresh_title_bar()
            return

        # History dropdown
        if v is None or v == _COMPARE_KEY_CURRENT:
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
        else:
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            self._select_snapshot_as_candidate(vid)
        if self._main_tab_index != TAB_HISTORY:
            self._main_tabs.selected_index = TAB_HISTORY
            if _ctrl_on_page(self._main_tabs):
                self._main_tabs.update()
            await self._sync_tab_switch_async(TAB_HISTORY)
        self._refresh_compare_diff_immediate()
        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()

    def _select_snapshot_as_candidate(self, vid: int) -> None:
        """Pick a snapshot row (History or Import). Auto-route to PDF/DOCX side-by-side when assets exist."""
        try:
            with session_scope() as s:
                body = version_storage.load_version_body(s, vid)
                pdf_rel = version_storage.get_version_pdf_relpath(s, vid)
                docx_rel = version_storage.get_version_docx_relpath(s, vid)
        except BaseException:
            self._snack("Could not load that version.")
            return
        self._compare_snapshot_version_id = vid
        self._pending_ai_accept_action_id = None
        self._compare_editor.value = body
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        if pdf_rel:
            self._compare_candidate_source = "pdf_original"
            self._compare_pdf_peer_snapshot_id = vid
            self._rebuild_compare_pdf_panes()
        elif docx_rel:
            self._compare_candidate_source = "docx_original"
            self._compare_pdf_peer_snapshot_id = vid
            self._rebuild_compare_docx_panes()
        else:
            self._compare_candidate_source = "snapshot"
            self._compare_pdf_peer_snapshot_id = None
            self._rebuild_compare_paragraph_ui()
        self._sync_compare_pdf_layers_visibility()

    def _on_main_tabs_change(self, e: ft.ControlEvent) -> None:
        try:
            new_ix = int(e.data)
        except (TypeError, ValueError):
            new_ix = int(self._main_tabs.selected_index)
        self.page.run_task(self._sync_tab_switch_async, new_ix)

    def _refresh_tab_toolbar(self) -> None:
        """Switch toolbar slot visibility."""
        self._toolbar_history.visible = self._main_tab_index == TAB_HISTORY
        self._toolbar_focus_area.visible = self._main_tab_index == TAB_PRESENT
        self._toolbar_review.visible = self._main_tab_index == TAB_FUTURE
        if _ctrl_on_page(self._tab_toolbar):
            self._tab_toolbar.update()

    def _apply_compare_candidate_dropdown_tab_chrome(self) -> None:
        """Tint the History version dropdown based on selection and hover state."""
        selected = self._main_tab_index == TAB_HISTORY
        union_hover = self._compare_tab_bar_hover_index1 or self._compare_dropdown_hover

        if selected:
            base_fill = _blend_hex_rgb(config.SURFACE, config.FEDORA_BLUE, 0.14)
        else:
            base_fill = config.SURFACE

        fill = _blend_hex_rgb(base_fill, "#FFFFFF", 0.085) if union_hover else base_fill
        self._compare_candidate_dropdown.fill_color = fill

        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()

    def _on_main_tab_bar_hover(self, e: ft.TabBarHoverEvent) -> None:
        self._compare_tab_bar_hover_index1 = e.index == TAB_HISTORY and e.hovering
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _on_compare_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()

    async def _sync_tab_switch_async(self, new_ix: int) -> None:
        prev = self._main_tab_index
        if new_ix == prev:
            return

        self._cancel_autosave_timers()
        if self.current_path and self._is_dirty():
            await self.save_file(silent=True, snapshot_reason="pre_switch")

        # Leaving Present: nothing to flush (editor is the source of truth).
        # Leaving History: right fields are the current draft — already in editor.value via
        #   _on_compare_para_field_change → _on_future_left_field_change isn't used here; nothing to flush.
        # Leaving Future: left fields were synced to editor on each keystroke.

        # Entering History (TAB_HISTORY): prefer 2nd-newest history autosave (newest ≈ draft), else rebuild.
        if new_ix == TAB_HISTORY:
            pick_vid: int | None = None
            if self.current_path and prev != TAB_HISTORY:
                with session_scope() as s:
                    snaps = version_storage.list_snapshots(s, self.current_path.resolve())
                pick_vid = version_storage.second_newest_history_autosave_version_id(snaps)

            if pick_vid is not None:
                self._select_snapshot_as_candidate(pick_vid)
                self._capture_compare_baseline_snapshot()
            else:
                if self._compare_candidate_source not in ("snapshot", "pdf_original", "docx_original"):
                    # No snapshot selected yet; prime left from draft until user picks a version.
                    self._compare_candidate_source = "snapshot"
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

        # Entering Present (TAB_PRESENT)
        elif new_ix == TAB_PRESENT:
            self._margin_gen += 1
            # editor.value is authoritative; rebuild margin sparkle
            await self._debounced_compose_rebuild(self._margin_gen)

        # Entering Future (TAB_FUTURE): keep ai_preview if already staged, else prime.
        elif new_ix == TAB_FUTURE:
            already_staged = (
                self._compare_candidate_source == "ai_preview"
                and self._pending_ai_accept_action_id
            )
            if not already_staged:
                self._compare_candidate_source = "ai_preview"
                # Prime compare_editor with current text so Future shows current vs current (no changes)
                # until a real AI proposal is staged.
                if not self._compare_editor.value:
                    self._compare_editor.value = self.editor.value or ""
            self._rebuild_future_paragraph_ui()

        self._main_tab_index = new_ix
        if new_ix == TAB_PRESENT:
            self._compare_tab_bar_hover_index1 = False
            self._compare_dropdown_hover = False
        self._apply_compare_candidate_dropdown_tab_chrome()
        self._refresh_compare_tab_candidate_ui()
        if new_ix == TAB_HISTORY:
            self._refresh_plan_compare_bar()
        self._refresh_tab_toolbar()
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

    @staticmethod
    def _compare_displacement_arrow_text(displacement: int | None) -> str:
        if displacement is None or displacement == 0:
            return ""
        n = abs(displacement)
        return f"↑{n}" if displacement > 0 else f"↓{n}"

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

    def _make_compare_pill_row(
        self, kind: paragraph_compare.SlotKind, displacement: int | None
    ) -> ft.Row:
        arrow = self._compare_displacement_arrow_text(displacement)
        arrow_ctrl = ft.Container(
            content=ft.Text(
                arrow,
                size=11,
                weight=ft.FontWeight.W_500,
                color=ft.Colors.GREY_500,
                font_family="monospace",
                text_align=ft.TextAlign.CENTER,
            ),
            width=34,
            alignment=ft.Alignment.CENTER,
            padding=ft.padding.only(top=2),
        )
        return ft.Row(
            [arrow_ctrl, self._make_compare_pill(kind)],
            spacing=2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _sync_compare_buffer_from_fields(self) -> None:
        parts = [tf.value or "" for tf in self._compare_right_fields]
        merged = "\n\n".join(parts) if parts else ""
        if self._main_tab_index == TAB_HISTORY:
            # History right fields = current draft → sync back to editor
            self.editor.value = merged
        else:
            self._compare_editor.value = merged

    def _capture_compare_baseline_snapshot(self) -> None:
        """Store Compose buffer as the Compare left baseline (draft mode only uses this via _compare_latest_baseline_text)."""
        self._compare_baseline_snapshot = self.editor.value or ""

    def _compare_latest_baseline_text(self) -> str:
        """Baseline text for checks / diff: always the current draft in editor."""
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

        ``left_para`` / ``right_para`` match ``pair_paragraphs_for_compare`` (baseline slot ``i``
        vs candidate slot ``i``). ``build_unified_spans`` expects (old, new), so we pass
        ``(right_para, left_para)`` — candidate as old, draft baseline as new for highlight semantics.
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

    def _build_actions_square(
        self,
        i: int,
        *,
        persistent: bool = False,
    ) -> tuple[ft.Container, ft.Container | None]:
        """Build the action rectangle (check/close/play/sparkle). Returns (actions_ctrl, hover_wrap_or_None)."""
        spark = self._paragraph_sparkle_menu_control(i, for_compare=True, compact=True)
        row_h = COMPARE_ACTION_GRID_CELL
        actions_inner = build_action_rectangle(
            top_left=action_rail_approve_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_accept_paragraph_async, ix),
            ),
            top_right=action_rail_reject_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_decline_paragraph_async, ix),
            ),
            bottom_left=action_rail_play_icon_button(
                on_click=lambda _e: self.page.run_task(self._open_project_page),
            ),
            bottom_right=spark,
            row_h=row_h,
        )
        return wrap_workspace_action_chrome(actions_inner, persistent=persistent)

    def _rebuild_compare_paragraph_ui(self) -> None:
        """History tab: old(diff, read-only) | pill | current(editable); equal-width columns, no action rail."""
        self._compare_pill_gen += 1
        # History: left = snapshot (old), right = current draft
        snapshot_text = self._compare_editor.value or ""
        current_text = self.editor.value or ""
        if len(snapshot_text) + len(current_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            snapshot_text = snapshot_text[:half] + "\n…"
            current_text = current_text[:half] + "\n…"
        pairs = pair_paragraphs_for_compare(snapshot_text, current_text)
        # stable_texts = current paragraphs (for decline = undo right-field edits)
        current_paras = split_paragraphs(current_text)
        self._compare_row_stable_texts = [
            current_paras[i] if i < len(current_paras) else "" for i in range(len(pairs))
        ]
        kinds_h, disps_h = paragraph_compare.compare_slots_heuristic(snapshot_text, current_text)

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
        # Refresh paragraph hash list against the current text; resize results buffers.
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
        for i, (old_txt, cur_txt) in enumerate(pairs):
            kind = kinds_h[i] if i < len(kinds_h) else "unchanged"
            disp = disps_h[i] if i < len(disps_h) else None
            pill_host = ft.Container(
                content=self._make_compare_pill_row(kind, disp),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4),
            )
            # Left: old/snapshot with diff spans highlighting what changed
            left_diff = ft.Text(
                spans=self._compare_paragraph_diff_spans(old_txt, cur_txt),
                selectable=True,
                expand=True,
            )
            self._compare_left_diff_texts.append(left_diff)
            left_cell = ft.Container(
                content=left_diff,
                expand=1,
                padding=ft.padding.all(8),
            )
            # Right: current draft (editable)
            right_tf = ft.TextField(
                **shared_tf_kwargs,
                value=cur_txt,
                read_only=False,
                enable_interactive_selection=True,
                hint_text="…",
                expand=True,
                on_change=lambda _e, ix=i: self._on_compare_para_field_change(ix),
            )

            # History row: old(diff) | pill (fixed) | draft — left/right expand equally
            row_inner = ft.Row(
                [
                    left_cell,
                    pill_host,
                    ft.Container(right_tf, expand=1),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            self._compare_rows_listview.controls.append(row_inner)
            self._compare_right_fields.append(right_tf)
            self._compare_row_pill_hosts.append(pill_host)

        if _ctrl_on_page(self._compare_rows_listview):
            self._compare_rows_listview.update()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _rebuild_future_paragraph_ui(self) -> None:
        """Future tab: current(editable) | pill | AI proposal(read-only) | actions(persistent)."""
        self._compare_pill_gen += 1
        # Future: left = current draft, right = AI proposal
        current_text = self.editor.value or ""
        ai_text = self._compare_editor.value or ""
        if len(current_text) + len(ai_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current_text = current_text[:half] + "\n…"
            ai_text = ai_text[:half] + "\n…"
        pairs = pair_paragraphs_for_compare(current_text, ai_text)
        # stable_texts = current paragraphs (for decline = reject AI, revert right to current)
        current_paras = split_paragraphs(current_text)
        self._compare_row_stable_texts = [
            current_paras[i] if i < len(current_paras) else "" for i in range(len(pairs))
        ]
        kinds_h, disps_h = paragraph_compare.compare_slots_heuristic(current_text, ai_text)

        self._future_rows_listview.controls.clear()
        self._future_left_fields.clear()
        self._future_right_diff_texts.clear()
        self._future_row_pill_hosts.clear()
        # Reuse _compare_right_fields for accept/decline handlers (right = AI proposal)
        self._compare_right_fields.clear()
        self._compare_eval_hosts.clear()

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

        for i, (cur_txt, ai_txt) in enumerate(pairs):
            kind = kinds_h[i] if i < len(kinds_h) else "unchanged"
            disp = disps_h[i] if i < len(disps_h) else None
            pill_host = ft.Container(
                content=self._make_compare_pill_row(kind, disp),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4),
            )
            # Left: current draft (editable)
            left_tf = ft.TextField(
                **shared_tf_kwargs,
                value=cur_txt,
                read_only=False,
                enable_interactive_selection=True,
                hint_text="…",
                expand=True,
                on_change=lambda _e, ix=i: self._on_future_left_field_change(ix),
            )
            # Right: AI proposal (read-only, shown with diff spans)
            right_diff = ft.Text(
                spans=self._compare_paragraph_diff_spans(ai_txt, cur_txt),
                selectable=True,
                expand=True,
            )
            right_cell = ft.Container(
                content=right_diff,
                expand=1,
                padding=ft.padding.all(8),
            )
            # Also keep a read-only TextField so accept handler can read AI text value
            right_tf_ro = ft.TextField(
                **shared_tf_kwargs,
                value=ai_txt,
                read_only=True,
                visible=False,
                height=0,
                width=0,
            )
            if show_actions:
                actions_ctrl, hover_wrap_future = self._build_actions_square(i, persistent=False)
            else:
                actions_ctrl, hover_wrap_future = None, None

            # Review row: current(editable) | pill | AI(read-only diff) | actions(hover)
            row_cells: list[ft.Control] = [
                ft.Container(left_tf, expand=1),
                pill_host,
                right_cell,
            ]
            if actions_ctrl is not None:
                row_cells.append(actions_ctrl)
            row_inner = ft.Row(
                row_cells,
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            if hover_wrap_future is not None:
                row = ft.Container(
                    content=row_inner,
                    on_hover=lambda e, w=hover_wrap_future: self._on_compare_row_hover(e, w),
                )
            else:
                row = row_inner
            self._future_rows_listview.controls.append(row)
            self._future_left_fields.append(left_tf)
            self._future_right_diff_texts.append(right_diff)
            self._future_row_pill_hosts.append(pill_host)
            # _compare_right_fields tracks AI text for accept/decline handlers
            self._compare_right_fields.append(right_tf_ro)

        if _ctrl_on_page(self._future_rows_listview):
            self._future_rows_listview.update()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _on_future_left_field_change(self, index: int) -> None:
        """Sync Future left field edits back to editor buffer and refresh title."""
        parts = [tf.value or "" for tf in self._future_left_fields]
        new_val = "\n\n".join(parts) if parts else ""
        self.editor.value = new_val
        self._refresh_title_bar()
        self._kick_debounced_autosave()

    def _refresh_compare_diff_immediate(self) -> None:
        if self._main_tab_index == TAB_FUTURE:
            self._rebuild_future_paragraph_ui()
            return
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

    def _active_pill_hosts(self) -> list:
        """Return the pill host list for the currently active compare tab."""
        if self._main_tab_index == TAB_FUTURE:
            return self._future_row_pill_hosts
        return self._compare_row_pill_hosts

    async def _debounced_compare_pill_refresh(self, gen: int) -> None:
        await asyncio.sleep(0.18)
        if gen != self._compare_pill_gen:
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        if self._main_tab_index == TAB_HISTORY:
            old_text = self._compare_editor.value or ""
            new_text = self.editor.value or ""
        else:
            new_text = self._compare_editor.value or ""
            old_text = self.editor.value or ""
        kinds, disps = paragraph_compare.compare_slots_heuristic(old_text, new_text)
        for i, host in enumerate(self._active_pill_hosts()):
            k = kinds[i] if i < len(kinds) else "unchanged"
            d = disps[i] if i < len(disps) else None
            host.content = self._make_compare_pill_row(k, d)
            if _ctrl_on_page(host):
                host.update()

    async def _debounced_refine_compare_slots(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._compare_refine_gen:
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        if self._main_tab_index == TAB_HISTORY:
            baseline = self._compare_editor.value or ""
            candidate = self.editor.value or ""
        else:
            baseline = self.editor.value or ""
            candidate = self._compare_editor.value or ""
        if not baseline.strip() and not candidate.strip():
            return
        try:
            refined, aligned_lefts, disps_ref = await paragraph_compare.classify_slots_async(
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
        for i, host in enumerate(self._active_pill_hosts()):
            k = refined[i] if i < len(refined) else "unchanged"
            d = disps_ref[i] if i < len(disps_ref) else None
            host.content = self._make_compare_pill_row(k, d)
            if _ctrl_on_page(host):
                host.update()
        # Update diff spans in History left column after AI refinement
        if self._main_tab_index == TAB_HISTORY and (
            len(aligned_lefts) == len(self._compare_left_diff_texts)
            and len(aligned_lefts) == len(self._compare_right_fields)
        ):
            for i, left_txt in enumerate(aligned_lefts):
                right_txt = self._compare_right_fields[i].value or ""
                t = self._compare_left_diff_texts[i]
                t.spans = self._compare_paragraph_diff_spans(left_txt, right_txt)
                if _ctrl_on_page(t):
                    t.update()

    def _on_compare_para_field_change(self, index: int) -> None:
        self._sync_compare_buffer_from_fields()
        # In History, right fields track current draft (editor); in other modes, compare_editor.
        if self._main_tab_index == TAB_HISTORY:
            cand = self.editor.value or ""
        else:
            cand = self._compare_editor.value or ""
        n_para = len(split_paragraphs(cand))
        if n_para != len(self._compare_right_fields):
            self._rebuild_compare_paragraph_ui()
            self._refresh_title_bar()
            self._kick_debounced_autosave()
            return
        self._refresh_title_bar()
        self._compare_diff_gen += 1
        dgen = self._compare_diff_gen
        self.page.run_task(self._debounced_compare_diff, dgen)
        self._kick_debounced_autosave()

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
        self._refresh_compare_diff_immediate()
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
        self._refresh_compare_diff_immediate()
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
        self._refresh_compare_diff_immediate()
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
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()
        self._snack("All paragraphs reset to match latest.")

    def _hide_prompt_footer(self, footer: ft.Row) -> None:
        footer.controls.clear()
        footer.visible = False
        if _ctrl_on_page(footer):
            footer.update()

    async def _apply_margin_reply_to_selection_async(
        self,
        reply: ft.Text,
        footer: ft.Row,
        action_id: str,
        span: tuple[int, int],
        original_snippet: str,
    ) -> None:
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        a, b = int(span[0]), int(span[1])
        buf = self.editor.value or ""
        if a < 0 or b > len(buf) or a > b:
            self._snack("Selection range is no longer valid.")
            self._hide_prompt_footer(footer)
            return
        if buf[a:b] != original_snippet:
            self._snack("Document changed; cannot apply to original selection.")
            return
        act = prompts.get_margin_action(action_id)
        if self.current_path:
            try:
                with session_scope() as s:
                    version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        buf,
                        "ai_apply",
                        display_label=f"{act.label} · apply" if act else "AI · apply",
                    )
            except BaseException:
                pass
        self.editor.value = buf[:a] + text + buf[b:]
        self._compose_sel_span = None
        self._hide_prompt_footer(footer)
        if _ctrl_on_page(self.editor):
            self.editor.update()
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_title_bar()
        self._snack("Selection replaced.")

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
        self._refresh_tab_toolbar()
        self._refresh_compare_tab_candidate_ui()
        self._compare_candidate_dropdown.value = _COMPARE_KEY_CANDIDATE
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

        self._append_chat_line(
            "user", f"Compare · paragraph {idx + 1}: {act.label}", quote=para
        )

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
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=ft.Colors.GREY_400,
                    tooltip="Dismiss",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
                ),
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
                ft.IconButton(
                    ft.Icons.VISIBILITY_OUTLINED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=ft.Colors.GREY_400,
                    tooltip="Review: stage this text as the Compare candidate for this paragraph",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                        self._stage_compare_margin_review_async, i, r, f, aid
                    ),
                ),
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=ft.Colors.GREY_400,
                    tooltip="Dismiss",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
                ),
            ]
        else:
            footer.controls = [
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=ft.Colors.GREY_400,
                    tooltip="Dismiss",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, f=footer: self._hide_prompt_footer(f),
                ),
            ]
        footer.visible = True
        if _ctrl_on_page(footer):
            footer.update()

    async def _stage_ai_candidate_async(self, idx: int, reply: ft.Text, footer: ft.Row, action_id: str) -> None:
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        self._compose_sel_span = None
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
        # Set state before flipping the tab widget so _sync_tab_switch_async sees
        # the staged proposal immediately and doesn't reset _compare_editor.
        self._main_tab_index = TAB_FUTURE
        self._main_tabs.selected_index = TAB_FUTURE
        self._rebuild_future_paragraph_ui()
        self._refresh_tab_toolbar()
        self._refresh_compare_tab_candidate_ui()
        self._compare_candidate_dropdown.value = _COMPARE_KEY_CANDIDATE
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

