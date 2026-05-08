
"""Compare tab: candidates, paragraph rows, bulk AI accept."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft
import httpx

from iterthink import config
from iterthink import prompts
from iterthink.compare_layout import aligned_review_rows, pair_paragraphs_for_compare
from iterthink.diff_card import build_new_side_spans, build_old_side_spans, build_unified_spans
from iterthink.db.session import session_scope
from iterthink.paragraph_align import compute_hash
from iterthink import paragraph_compare
from iterthink import version_storage
from iterthink.version_storage import SnapshotInfo
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
    action_rail_reject_icon_button,
    build_action_square,
)
from iterthink.studio_focus_area import (
    _ki_topic_index_for_prompt_topic,
    _strip_change_topic_preamble,
)
from iterthink import ui_theme
from iterthink.studio_constants import (
    COMPARE_ACTION_GRID_CELL,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_KEY_CURRENT as _COMPARE_KEY_CURRENT,
    COMPARE_PILL_COL_W,
    DIFF_SPAN_CHAR_CAP as _DIFF_SPAN_CHAR_CAP,
    PROJECT_PAGE_URL as _PROJECT_PAGE_URL,
    TAB_HISTORY,
    TAB_PRESENT,
    TAB_FUTURE,
)
from iterthink.studio_util import ctrl_on_page as _ctrl_on_page

# Outer padding only on both History columns so Text and TextField share the same inner width.
_COMPARE_HISTORY_CELL_PAD = ft.padding.all(8)


class MarkdownStudioCompareText:
    def _working_document_text(self) -> str:
        """Text compared to on-disk `last_saved_text` for dirty + save."""
        return self.editor.value or ""

    def _is_dirty(self) -> bool:
        return self._working_document_text() != self.last_saved_text

    def _editor_buffer(self) -> str:
        if self._main_tab_index == TAB_HISTORY:
            return self._history_newer_side_text() or ""
        if self._main_tab_index == TAB_FUTURE:
            return self._compare_editor.value or ""
        return self.editor.value or ""

    @staticmethod
    def _history_compare_snapshots(snaps: list[SnapshotInfo]) -> list[SnapshotInfo]:
        return [s for s in snaps if s.reason != "ai_proposal"]

    @staticmethod
    def _snapshots_strictly_older_than(
        newest_first: list[SnapshotInfo], newer_version_id: int | None
    ) -> list[SnapshotInfo]:
        if newer_version_id is None:
            return list(newest_first)
        for i, s in enumerate(newest_first):
            if s.version_id == newer_version_id:
                return newest_first[i + 1 :]
        return list(newest_first)

    def _history_newer_side_text(self) -> str:
        if self._compare_newer_version_id is None:
            return self.editor.value or ""
        return self._compare_newer_cached_body or ""

    def _history_default_older_version_id(
        self, snaps_all: list[SnapshotInfo], older_slice: list[SnapshotInfo]
    ) -> int | None:
        if not older_slice:
            return None
        allowed = {s.version_id for s in older_slice}
        pick = version_storage.second_newest_history_autosave_version_id(snaps_all)
        if pick is not None and pick in allowed:
            return pick
        return older_slice[0].version_id

    def _refresh_compare_tab_candidate_ui(self) -> None:
        _st = ui_theme.compare_candidate_dropdown_option_style()

        # History / Review dropdowns live in a Stack under the tab bar; only the active tab's
        # toolbar is visible. Mutating + .update() on a hidden Dropdown can surface its menu
        # overlay (e.g. History flash on Focus Area). Gate each block to its tab.
        if self._main_tab_index == TAB_HISTORY:
            # ── History: newer (right) + older (left) dropdowns ─────────────────────

            def snapshot_option_rows(slist: list[SnapshotInfo]) -> list[ft.dropdown.Option]:
                snap_opts: list[ft.dropdown.Option] = []
                import_opts: list[ft.dropdown.Option] = []
                for sn in slist:
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
                snap_opts.extend(import_opts)
                return snap_opts

            snaps_all: list[SnapshotInfo] = []
            if self.current_path:
                with session_scope() as s:
                    snaps_all = version_storage.list_snapshots(s, self.current_path.resolve())
            filt = self._history_compare_snapshots(snaps_all)

            newer_opts: list[ft.dropdown.Option] = [
                ft.dropdown.Option(key=_COMPARE_KEY_CURRENT, text="Current draft", style=_st)
            ]
            newer_opts.extend(snapshot_option_rows(filt))
            self._compare_newer_dropdown.options = newer_opts
            newer_keys = {o.key for o in newer_opts}
            if self._compare_newer_version_id is not None:
                nk = str(self._compare_newer_version_id)
                if nk in newer_keys:
                    self._compare_newer_dropdown.value = nk
                else:
                    self._compare_newer_version_id = None
                    self._compare_newer_cached_body = ""
                    self._compare_newer_dropdown.value = _COMPARE_KEY_CURRENT
            else:
                self._compare_newer_dropdown.value = _COMPARE_KEY_CURRENT

            if self._compare_newer_version_id is not None:
                try:
                    with session_scope() as s:
                        self._compare_newer_cached_body = version_storage.load_version_body(
                            s, self._compare_newer_version_id
                        )
                except BaseException:
                    self._compare_newer_cached_body = ""

            older_slice = self._snapshots_strictly_older_than(filt, self._compare_newer_version_id)
            older_opts = snapshot_option_rows(older_slice)
            self._compare_candidate_dropdown.options = older_opts
            o_keys = {o.key for o in older_opts}

            cur_old = self._compare_snapshot_version_id
            sk = str(cur_old) if cur_old is not None else None
            if sk in o_keys:
                self._compare_candidate_dropdown.value = sk
            elif older_slice:
                dv = self._history_default_older_version_id(snaps_all, older_slice)
                if dv is not None:
                    if cur_old != dv:
                        self._select_snapshot_as_candidate(dv)
                    self._compare_candidate_dropdown.value = str(dv)
                else:
                    self._compare_candidate_dropdown.value = None
            else:
                self._compare_snapshot_version_id = None
                self._compare_pdf_peer_snapshot_id = None
                self._pending_ai_accept_action_id = None
                self._compare_candidate_source = "snapshot"
                self._compare_editor.value = ""
                self._compare_candidate_dropdown.value = None

            if _ctrl_on_page(self._compare_newer_dropdown):
                self._compare_newer_dropdown.update()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()

        if self._main_tab_index == TAB_FUTURE:
            # ── Review dropdown: ai_proposal / legacy ai_staged + manual imports ─
            review_opts: list[ft.dropdown.Option] = []
            if self.current_path:
                with session_scope() as s:
                    snaps = version_storage.list_snapshots(s, self.current_path.resolve())
                for sn in snaps:
                    row_text = version_storage.snapshot_dropdown_text(sn)
                    if sn.reason == "ai_proposal":
                        review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"AI - {row_text}",
                                style=_st,
                            )
                        )
                    elif sn.reason == "ai_staged":
                        review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"AI - {row_text} (legacy)",
                                style=_st,
                            )
                        )
                    elif version_storage.snapshot_bucket(sn) == "import":
                        review_opts.append(
                            ft.dropdown.Option(
                                key=str(sn.version_id),
                                text=f"Import - {row_text}",
                                style=_st,
                            )
                        )
            self._review_candidate_dropdown.options = review_opts
            r_keys = {o.key for o in review_opts}
            # Default selection: currently-loaded ai_preview vid, else most recent ai_proposal, else first option.
            preferred: str | None = None
            if (
                self._compare_candidate_source == "ai_preview"
                and self._compare_snapshot_version_id is not None
            ):
                preferred = str(self._compare_snapshot_version_id)
            elif self._latest_ai_proposal_vid is not None:
                preferred = str(self._latest_ai_proposal_vid)
            if preferred and preferred in r_keys:
                self._review_candidate_dropdown.value = preferred
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
        self._compare_newer_dropdown.disabled = not has_doc
        self._compare_newer_dropdown.tooltip = (
            "Pick the newer version (right column, insertions). Default is the current draft."
            if has_doc
            else "Open a markdown file from the tree to list versions."
        )
        self._compare_candidate_dropdown.tooltip = (
            "Pick an older saved version (left column, deletions). Only versions older than the "
            "newer side are listed."
            if has_doc
            else "Open a markdown file from the tree to list versions."
        )
        if self._main_tab_index == TAB_HISTORY and _ctrl_on_page(self._compare_newer_dropdown):
            self._compare_newer_dropdown.update()
        if self._main_tab_index == TAB_HISTORY and _ctrl_on_page(self._compare_candidate_dropdown):
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

    async def _on_compare_newer_change_async(self, e: ft.ControlEvent) -> None:
        if e.control is not self._compare_newer_dropdown or self._compare_newer_dropdown.disabled:
            return
        if not self.current_path:
            return
        v = e.control.value
        if v == _COMPARE_KEY_CURRENT:
            self._compare_newer_version_id = None
            self._compare_newer_cached_body = ""
        else:
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            self._compare_newer_version_id = vid
            try:
                with session_scope() as s:
                    self._compare_newer_cached_body = version_storage.load_version_body(s, vid)
            except BaseException:
                self._compare_newer_version_id = None
                self._compare_newer_cached_body = ""
                self._snack("Could not load that version.")
                self._refresh_compare_tab_candidate_ui()
                return
        self._refresh_compare_tab_candidate_ui()
        self._capture_compare_baseline_snapshot()
        self._sync_compare_pdf_layers_visibility()
        self._refresh_compare_diff_immediate()
        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()

    async def _on_compare_candidate_change_async(self, e: ft.ControlEvent) -> None:
        from_review = e.control is self._review_candidate_dropdown
        if from_review:
            # Review dropdown: ai_proposal / legacy ai_staged snapshot or import — stays on Review tab.
            v = e.control.value
            if v is None:
                return
            try:
                vid = int(v)
            except (TypeError, ValueError):
                return
            # Persist edits to the currently-loaded proposal before swapping it out.
            self._flush_review_edits_if_changed()
            with session_scope() as s:
                row = version_storage.get_version_row(s, vid)
            if row is not None and row.reason in ("ai_proposal", "ai_staged"):
                self._select_proposal_as_review_candidate(vid)
            else:
                # Imports: load as candidate, then treat as ai_preview so accept goes through AI flow.
                self._select_snapshot_as_candidate(vid)
                self._compare_candidate_source = "ai_preview"
                self._pending_ai_accept_action_id = (
                    self._pending_ai_accept_action_id or "ai_proposal"
                )
                self._loaded_proposal_sha = version_storage.content_sha256(
                    self._compare_editor.value or ""
                )
            self._rebuild_future_paragraph_ui()
            self._refresh_compare_tab_candidate_ui()
            self._refresh_compare_diff_immediate()
            self._refresh_compare_bulk_buttons()
            self._refresh_title_bar()
            return

        if e.control is self._compare_newer_dropdown:
            await self._on_compare_newer_change_async(e)
            return

        if self._compare_candidate_dropdown.disabled or not self.current_path:
            return
        v = e.control.value

        # History: older-side dropdown (snapshots strictly older than the newer pick)
        if v is None:
            return
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

    def _select_proposal_as_review_candidate(self, vid: int) -> None:
        """Load a persisted ai_proposal snapshot into the Review right column.

        Sets ai_preview state so per-row / bulk Accept go through the AI flow. The action_id
        used by Accept comes from the in-memory map (recorded on persist) or the snapshot's
        display_label as a fallback after restart; ``ai_proposal`` is the last-resort sentinel.
        """
        try:
            with session_scope() as s:
                body = version_storage.load_version_body(s, vid)
                row = version_storage.get_version_row(s, vid)
        except BaseException:
            self._snack("Could not load that proposal.")
            return
        self._compare_snapshot_version_id = vid
        self._compare_pdf_peer_snapshot_id = None
        self._compare_editor.value = body
        self._compare_candidate_source = "ai_preview"
        self._pending_ai_accept_action_id = (
            self._ai_proposal_action_ids.get(vid)
            or (row.display_label if row else None)
            or "ai_proposal"
        )
        self._loaded_proposal_sha = version_storage.content_sha256(body)
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()

    def _flush_review_edits_if_changed(self) -> None:
        """Persist edits to the loaded ai_proposal as a new snapshot when the user leaves it.

        SHA-dedup against the snapshot we loaded; no row is written for unchanged sessions.
        Updates _latest_ai_proposal_vid + _ai_proposal_action_ids + _compare_snapshot_version_id
        + _loaded_proposal_sha so dropdown / accept paths point at the new row.
        """
        if not self.current_path:
            return
        if self._compare_candidate_source != "ai_preview":
            return
        if self._loaded_proposal_sha is None:
            return
        body = self._compare_editor.value or ""
        new_sha = version_storage.content_sha256(body)
        if new_sha == self._loaded_proposal_sha:
            return
        aid = self._pending_ai_accept_action_id or ""
        act = prompts.get_margin_action(aid) if aid else None
        base_label = act.label if act else (aid or "AI proposal")
        label = f"{base_label} - edited"
        try:
            with session_scope() as s:
                new_vid = version_storage.persist_version_snapshot(
                    s,
                    self.current_path.resolve(),
                    body,
                    "ai_proposal",
                    display_label=label,
                )
        except BaseException:
            return
        if new_vid is None:
            # Most-recent doc snapshot already had this sha; treat as already saved.
            self._loaded_proposal_sha = new_sha
            return
        if aid:
            self._ai_proposal_action_ids[new_vid] = aid
        self._latest_ai_proposal_vid = new_vid
        self._compare_snapshot_version_id = new_vid
        self._loaded_proposal_sha = new_sha
        self._refresh_compare_tab_candidate_ui()

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

    def _build_review_subtab_button(self, label: str, idx: int) -> ft.Container:
        """Segmented Change/Impact button styled like a TabBar tab.

        Active tab gets the highlight underline; click swaps the panel + refreshes
        the toolbar so the Review chrome only shows on the Change subtab.
        """
        is_active = getattr(self, "_review_subtab_index", 0) == idx
        return ft.Container(
            content=ft.Text(
                label,
                size=13,
                weight=ft.FontWeight.W_500,
                color=config.ON_SURFACE if is_active else config.ON_SURFACE_VARIANT,
            ),
            expand=1,
            height=36,
            alignment=ft.Alignment.CENTER,
            on_click=lambda _e, i=idx: self._select_review_subtab(i),
            border=ft.border.only(
                bottom=ft.BorderSide(
                    2,
                    config.HIGHLIGHT if is_active else ft.Colors.TRANSPARENT,
                )
            ),
        )

    def _select_review_subtab(self, idx: int) -> None:
        if idx == self._review_subtab_index:
            return
        self._review_subtab_index = idx
        self._review_change_panel.visible = idx == 0
        self._review_impact_panel.visible = idx == 1
        if _ctrl_on_page(self._review_change_panel):
            self._review_change_panel.update()
        if _ctrl_on_page(self._review_impact_panel):
            self._review_impact_panel.update()
        self._refresh_review_subtab_strip()
        self._refresh_tab_toolbar()

    def _refresh_review_subtab_strip(self) -> None:
        """Sync visibility of the strip with the main tab + restyle active button."""
        on_review = self._main_tab_index == TAB_FUTURE
        self._review_subtab_strip.visible = on_review
        for idx, btn in (
            (0, self._review_subtab_change_btn),
            (1, self._review_subtab_impact_btn),
        ):
            is_active = idx == self._review_subtab_index
            text = btn.content
            if isinstance(text, ft.Text):
                text.color = (
                    config.ON_SURFACE if is_active else config.ON_SURFACE_VARIANT
                )
            btn.border = ft.border.only(
                bottom=ft.BorderSide(
                    2,
                    config.HIGHLIGHT if is_active else ft.Colors.TRANSPARENT,
                )
            )
            if _ctrl_on_page(btn):
                btn.update()
        if _ctrl_on_page(self._review_subtab_strip):
            self._review_subtab_strip.update()

    def _refresh_tab_toolbar(self) -> None:
        """Switch toolbar slot visibility."""
        on_history = self._main_tab_index == TAB_HISTORY
        on_review = (
            self._main_tab_index == TAB_FUTURE
            and getattr(self, "_review_subtab_index", 0) == 0
        )
        self._refresh_review_subtab_strip()
        self._toolbar_history.visible = on_history
        self._toolbar_focus_area.visible = self._main_tab_index == TAB_PRESENT
        self._toolbar_review.visible = on_review
        # Dropdown menus render in Flet's overlay layer, outside the toolbar Stack. Hide and
        # disable the inactive dropdown itself so a History menu cannot linger over Review.
        self._compare_candidate_dropdown.visible = on_history
        self._compare_newer_dropdown.visible = on_history
        lack_older = on_history and not bool(self._compare_candidate_dropdown.options)
        self._compare_candidate_dropdown.disabled = (
            (not on_history) or self.current_path is None or lack_older
        )
        self._compare_newer_dropdown.disabled = (not on_history) or self.current_path is None
        self._review_candidate_dropdown.visible = on_review
        self._review_candidate_dropdown.disabled = (not on_review) or not bool(
            self._review_candidate_dropdown.options
        )
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()
        if _ctrl_on_page(self._compare_newer_dropdown):
            self._compare_newer_dropdown.update()
        if _ctrl_on_page(self._review_candidate_dropdown):
            self._review_candidate_dropdown.update()
        if _ctrl_on_page(self._tab_toolbar):
            self._tab_toolbar.update()

    def _set_compare_version_dd_focused(self, focused: bool) -> None:
        self._compare_version_dd_focused = focused
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _apply_compare_candidate_dropdown_tab_chrome(self) -> None:
        """Outline around the version dropdown (tree search style): grey at rest, blue on hover/focus."""
        selected = self._main_tab_index == TAB_HISTORY
        union_hover = (
            self._compare_tab_bar_hover_index1
            or self._compare_dropdown_hover
            or self._compare_newer_dropdown_hover
        )
        accent = selected and (union_hover or self._compare_version_dd_focused)
        rim = config.PRIMARY_COLOR if accent else ui_theme.outline_muted()
        for wrap in (self._compare_dropdown_hover_wrap, self._compare_newer_dropdown_hover_wrap):
            wrap.border = ft.Border.all(1, rim)
        if self._main_tab_index == TAB_HISTORY:
            if _ctrl_on_page(self._compare_dropdown_hover_wrap):
                self._compare_dropdown_hover_wrap.update()
            if _ctrl_on_page(self._compare_newer_dropdown_hover_wrap):
                self._compare_newer_dropdown_hover_wrap.update()
            if _ctrl_on_page(self._compare_candidate_dropdown):
                self._compare_candidate_dropdown.update()
            if _ctrl_on_page(self._compare_newer_dropdown):
                self._compare_newer_dropdown.update()

    def _on_main_tab_bar_hover(self, e: ft.TabBarHoverEvent) -> None:
        self._compare_tab_bar_hover_index1 = e.index == TAB_HISTORY and e.hovering
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _on_compare_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()

    def _on_compare_newer_dropdown_container_hover(self, e: ft.ControlEvent) -> None:
        self._compare_newer_dropdown_hover = str(e.data).lower() == "true"
        self._apply_compare_candidate_dropdown_tab_chrome()

    async def _sync_tab_switch_async(self, new_ix: int) -> None:
        prev = self._main_tab_index
        if new_ix == prev:
            return

        self._cancel_autosave_timers()
        if self.current_path and self._is_dirty():
            await self.save_file(silent=True, snapshot_reason="pre_switch")
        # Persist any in-flight Review proposal edits before switching away from Future.
        if prev == TAB_FUTURE:
            self._flush_review_edits_if_changed()

        # Leaving Present: nothing to flush (editor is the source of truth).
        # Leaving History: right fields are read-only carriers; current draft is editor.value.
        # Leaving Future: right fields are the AI proposal candidate, kept in _compare_editor.value
        #   in-memory; nothing to persist until Accept.

        # Entering History (TAB_HISTORY): prefer 2nd-newest history autosave (newest ≈ draft), else rebuild.
        if new_ix == TAB_HISTORY:
            if prev != TAB_HISTORY:
                self._compare_newer_version_id = None
                self._compare_newer_cached_body = ""
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

        # Entering Future (TAB_FUTURE): auto-load the most recent ai_proposal / legacy ai_staged when nothing is staged.
        elif new_ix == TAB_FUTURE:
            already_staged = (
                self._compare_candidate_source == "ai_preview"
                and self._pending_ai_accept_action_id
                and self._compare_snapshot_version_id is not None
            )
            if not already_staged:
                target_vid = self._latest_ai_proposal_vid
                if target_vid is None and self.current_path:
                    with session_scope() as s:
                        snaps = version_storage.list_snapshots(s, self.current_path.resolve())
                    for sn in snaps:  # newest first
                        if sn.reason in ("ai_proposal", "ai_staged"):
                            target_vid = sn.version_id
                            break
                if target_vid is not None:
                    self._select_proposal_as_review_candidate(target_vid)
                    self._latest_ai_proposal_vid = target_vid
                else:
                    # No proposals yet: prime with current draft so Review shows current vs current.
                    self._compare_candidate_source = "ai_preview"
                    if not self._compare_editor.value:
                        self._compare_editor.value = self.editor.value or ""
                    self._loaded_proposal_sha = version_storage.content_sha256(
                        self._compare_editor.value or ""
                    )
            self._rebuild_future_paragraph_ui()

        self._main_tab_index = new_ix
        self._hide_all_result_card_overlays()
        if new_ix != TAB_HISTORY:
            self._compare_version_dd_focused = False
            self._compare_dropdown_hover = False
            self._compare_newer_dropdown_hover = False
        if new_ix == TAB_PRESENT:
            self._compare_tab_bar_hover_index1 = False
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
            color=config.ON_SURFACE,
        )

    def _compare_insertion_diff_colors(self) -> tuple[str, str | None]:
        """Insertion spans: foreground always ``on_surface``; light uses ``success`` tint as bg."""
        if config.IS_LIGHT:
            return config.ON_SURFACE, ft.Colors.with_opacity(0.5, config.SUCCESS)
        return config.ON_SURFACE, None

    def _compare_pill_colors(self, kind: paragraph_compare.SlotKind) -> tuple[str, str]:
        new_pill: tuple[str, str] = (
            (ft.Colors.with_opacity(0.45, config.SUCCESS), config.ON_SURFACE)
            if config.IS_LIGHT
            else (ft.Colors.with_opacity(0.28, ft.Colors.GREEN_400), ft.Colors.GREEN_100)
        )
        m: dict[str, tuple[str, str]] = {
            "unchanged": (
                ft.Colors.with_opacity(0.18, config.OUTLINE),
                config.ON_SURFACE_VARIANT,
            ),
            "minor": (ft.Colors.with_opacity(0.28, ft.Colors.BLUE_400), ft.Colors.BLUE_100),
            "major": (ft.Colors.with_opacity(0.28, ft.Colors.ORANGE_400), ft.Colors.ORANGE_100),
            "rewritten": (ft.Colors.with_opacity(0.28, ft.Colors.PURPLE_400), ft.Colors.PURPLE_100),
            "new": new_pill,
            "deleted": (ft.Colors.with_opacity(0.28, ft.Colors.RED_400), ft.Colors.RED_100),
        }
        return m.get(
            kind,
            (ft.Colors.with_opacity(0.22, config.OUTLINE), config.ON_SURFACE_VARIANT),
        )

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
                color=config.ON_SURFACE_VARIANT,
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
        if self._main_tab_index == TAB_FUTURE:
            # Drop empty entries: a delete row that's accepted (field stays "") removes
            # the paragraph from the buffer; an insert row that's declined ("" set on
            # decline) does the same — naturally yielding the post-accept document.
            merged = "\n\n".join(p for p in parts if p)
        else:
            merged = "\n\n".join(parts) if parts else ""
        if self._main_tab_index == TAB_HISTORY:
            if self._compare_newer_version_id is None:
                self.editor.value = merged
            else:
                self._compare_newer_cached_body = merged
        else:
            self._compare_editor.value = merged

    def _capture_compare_baseline_snapshot(self) -> None:
        """Store Compose buffer as the Compare left baseline (draft mode only uses this via _compare_latest_baseline_text)."""
        self._compare_baseline_snapshot = self.editor.value or ""

    def _compare_latest_baseline_text(self) -> str:
        """On History, the newer (right) side; otherwise the compose document."""
        if self._main_tab_index == TAB_HISTORY:
            return self._history_newer_side_text()
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
        """Unified inline diff (Review right column): both deletions and insertions in one stream."""
        old_t, new_t = self._compare_diff_clip(left_para, right_para)
        ins_fg, ins_bg = self._compare_insertion_diff_colors()
        return build_unified_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=config.ON_SURFACE,
            font_family="monospace",
            insert_color=ins_fg,
            insert_bgcolor=ins_bg,
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    @staticmethod
    def _compare_diff_clip(left_para: str, right_para: str) -> tuple[str, str]:
        cap = 80_000
        if len(left_para) + len(right_para) > cap:
            half = cap // 2
            return left_para[:half] + "\n…", right_para[:half] + "\n…"
        return left_para, right_para

    def _compare_old_side_spans(self, old_para: str, new_para: str) -> list[ft.TextSpan]:
        """History left column: only words from the snapshot, deletions struck through."""
        old_t, new_t = self._compare_diff_clip(old_para, new_para)
        return build_old_side_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=config.ON_SURFACE,
            font_family="monospace",
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    def _future_old_side_spans(self, cur_para: str, ai_para: str) -> list[ft.TextSpan]:
        """Future left column: words from current draft, deletions struck red.

        Caller convention for build_old_side_spans is (old, new). On Future, current text is
        the 'old' side and the AI proposal is the 'new' side, so removed-by-AI tokens get
        the strikethrough.
        """
        return self._compare_old_side_spans(cur_para, ai_para)

    def _compare_new_side_spans(self, old_para: str, new_para: str) -> list[ft.TextSpan]:
        """History right column: only words from the draft, insertions tinted green."""
        old_t, new_t = self._compare_diff_clip(old_para, new_para)
        ins_fg, ins_bg = self._compare_insertion_diff_colors()
        return build_new_side_spans(
            old_t,
            new_t,
            base_size=COMPARE_COL_FONT_SIZE,
            base_color=config.ON_SURFACE,
            font_family="monospace",
            insert_color=ins_fg,
            insert_bgcolor=ins_bg,
            line_height=COMPARE_COL_LINE_HEIGHT,
        )

    def _refresh_future_left_diff_spans(self) -> None:
        """Update Review left column diff spans for the gap-aligned row list."""
        if self._main_tab_index != TAB_FUTURE:
            return
        if len(self._future_left_diff_texts) != len(self._compare_right_fields):
            return
        current = self.editor.value or ""
        candidate = self._compare_editor.value or ""
        if len(current) + len(candidate) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current = current[:half] + "\n…"
            candidate = candidate[:half] + "\n…"
        rows = aligned_review_rows(current, candidate)
        for i, row in enumerate(rows):
            if i >= len(self._future_left_diff_texts):
                break
            t = self._future_left_diff_texts[i]
            if row.kind == "insert":
                t.spans = []
                t.value = ""
            else:
                t.value = None
                t.spans = self._future_old_side_spans(row.old_text, row.new_text)
            if _ctrl_on_page(t):
                t.update()

    def _refresh_compare_left_diff_spans(self) -> None:
        """Update History inline diff for both columns (snapshot vs draft; same paragraph count)."""
        if self._main_tab_index != TAB_HISTORY:
            return
        if len(self._compare_left_diff_texts) != len(self._compare_right_fields):
            return
        older = self._compare_editor.value or ""
        newer = self._history_newer_side_text() or ""
        if len(older) + len(newer) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            older = older[:half] + "\n…"
            newer = newer[:half] + "\n…"
        pairs = pair_paragraphs_for_compare(older, newer)
        for i, (left_txt, right_txt) in enumerate(pairs):
            if i >= len(self._compare_left_diff_texts):
                break
            left_t = self._compare_left_diff_texts[i]
            left_t.spans = self._compare_old_side_spans(left_txt, right_txt)
            if _ctrl_on_page(left_t):
                left_t.update()
            if i < len(self._compare_right_diff_texts):
                right_t = self._compare_right_diff_texts[i]
                right_t.spans = self._compare_new_side_spans(left_txt, right_txt)
                if _ctrl_on_page(right_t):
                    right_t.update()
            # Keep hidden carrier value in sync with the displayed draft paragraph.
            if i < len(self._compare_right_fields):
                self._compare_right_fields[i].value = right_txt

    def _build_actions_square(
        self,
        i: int,
        *,
        persistent: bool = False,
    ) -> tuple[ft.Container, ft.Container | None]:
        """Slim Review action grid: check + x only. Returns (actions_ctrl, hover_wrap_or_None)."""
        actions_inner = build_action_square(
            left=action_rail_approve_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_accept_paragraph_async, ix),
            ),
            right=action_rail_reject_icon_button(
                on_click=lambda _e, ix=i: self.page.run_task(self._compare_decline_paragraph_async, ix),
            ),
            row_h=COMPARE_ACTION_GRID_CELL,
        )
        return wrap_workspace_action_chrome(actions_inner, persistent=persistent)

    def _rebuild_compare_paragraph_ui(self) -> None:
        """History tab: eval | old(deletions) | pill | new(insertions) — diff columns read-only ft.Text."""
        self._compare_pill_gen += 1
        older_text = self._compare_editor.value or ""
        newer_text = self._history_newer_side_text()
        if len(older_text) + len(newer_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            older_text = older_text[:half] + "\n…"
            newer_text = newer_text[:half] + "\n…"
        pairs = pair_paragraphs_for_compare(older_text, newer_text)
        newer_paras = split_paragraphs(newer_text)
        self._compare_row_stable_texts = [
            newer_paras[i] if i < len(newer_paras) else "" for i in range(len(pairs))
        ]
        kinds_h, disps_h = paragraph_compare.compare_slots_heuristic(older_text, newer_text)

        self._compare_rows_listview.controls.clear()
        self._compare_right_fields.clear()
        self._compare_row_pill_hosts.clear()
        self._compare_left_diff_texts.clear()
        self._compare_right_diff_texts.clear()
        self._compare_eval_hosts.clear()
        # Hide any active result-card overlay; row layout is being rebuilt.
        self._hide_all_result_card_overlays()
        # Refresh paragraph hash list against the current text; resize results buffers.
        self._check_para_hashes = [compute_hash(new) for _, new in pairs]
        for cid in list(self._check_results.keys()):
            results = self._check_results.get(cid) or []
            if len(results) != len(pairs):
                self._check_results[cid] = (results + [None] * len(pairs))[: len(pairs)]

        para_style = self._compare_para_text_style()
        for i, (old_txt, cur_txt) in enumerate(pairs):
            kind = kinds_h[i] if i < len(kinds_h) else "unchanged"
            disp = disps_h[i] if i < len(disps_h) else None
            pill_host = ft.Container(
                content=self._make_compare_pill_row(kind, disp),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4),
            )
            # Left: snapshot, deletions only.
            left_diff = ft.Text(
                spans=self._compare_old_side_spans(old_txt, cur_txt),
                style=para_style,
                selectable=True,
                expand=True,
                no_wrap=False,
            )
            self._compare_left_diff_texts.append(left_diff)
            left_cell = ft.Container(
                content=left_diff,
                expand=1,
                padding=_COMPARE_HISTORY_CELL_PAD,
            )
            # Right: draft, insertions only (read-only).
            right_diff = ft.Text(
                spans=self._compare_new_side_spans(old_txt, cur_txt),
                style=para_style,
                selectable=True,
                expand=True,
                no_wrap=False,
            )
            self._compare_right_diff_texts.append(right_diff)
            right_cell = ft.Container(
                content=right_diff,
                expand=1,
                padding=_COMPARE_HISTORY_CELL_PAD,
            )
            # Hidden carrier so length-based code (hash invalidation, bulk-apply checks) stays unchanged.
            right_carrier = ft.TextField(
                value=cur_txt,
                visible=False,
                height=0,
                width=0,
            )

            eval_host = self._build_eval_cell(i)

            # History row: eval | old(deletions) | pill | new(insertions)
            row_inner = ft.Row(
                [
                    eval_host,
                    left_cell,
                    pill_host,
                    right_cell,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
            self._compare_rows_listview.controls.append(row_inner)
            self._compare_right_fields.append(right_carrier)
            self._compare_row_pill_hosts.append(pill_host)
            self._compare_eval_hosts.append(eval_host)

        if _ctrl_on_page(self._compare_rows_listview):
            self._compare_rows_listview.update()

        if self._active_check_id is not None:
            self._refresh_all_eval_cells()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

    def _rebuild_future_paragraph_ui(self) -> None:
        """Future tab: eval | current(read-only diff with deletions) | pill | AI proposal(editable) | actions.

        Rows are gap-aligned: pure deletions render with an empty right cell, pure
        insertions with an empty left cell. ``_compare_right_fields`` keeps one entry per
        UI row (a hidden empty TextField for delete rows) so accept/decline indices align
        with the visible row order. ``_future_row_kinds`` / ``_future_row_cand_idx`` /
        ``_future_row_stable_texts`` track per-row metadata for buffer sync and decline.
        """
        self._compare_pill_gen += 1
        current_text = self.editor.value or ""
        ai_text = self._compare_editor.value or ""
        if len(current_text) + len(ai_text) > _DIFF_SPAN_CHAR_CAP:
            half = _DIFF_SPAN_CHAR_CAP // 2
            current_text = current_text[:half] + "\n…"
            ai_text = ai_text[:half] + "\n…"
        rows = aligned_review_rows(current_text, ai_text)
        kinds_h, disps_h = paragraph_compare.compare_slots_heuristic(current_text, ai_text)

        self._future_rows_listview.controls.clear()
        self._future_left_diff_texts.clear()
        self._future_row_pill_hosts.clear()
        self._compare_right_fields.clear()
        self._compare_eval_hosts.clear()
        self._future_row_kinds = []
        self._future_row_cand_idx = []
        self._future_row_stable_texts = []
        self._hide_all_result_card_overlays()

        para_style = self._compare_para_text_style()
        right_tf_kwargs: dict[str, Any] = {
            "multiline": True,
            "max_lines": None,
            "min_lines": 1,
            "border": ft.InputBorder.NONE,
            "filled": False,
            "dense": True,
            "text_size": COMPARE_COL_FONT_SIZE,
            "text_style": para_style,
            "cursor_color": config.PRIMARY_COLOR,
            "selection_color": config.SELECTION_OVERLAY,
            "content_padding": ft.padding.all(0),
        }
        show_actions = bool(self.current_path)

        for i, row_spec in enumerate(rows):
            self._future_row_kinds.append(row_spec.kind)
            self._future_row_cand_idx.append(row_spec.cand_idx)
            self._future_row_stable_texts.append(row_spec.old_text)

            if row_spec.kind == "delete":
                pill_kind: paragraph_compare.SlotKind = "deleted"
                disp: int | None = None
            elif row_spec.cand_idx is not None and row_spec.cand_idx < len(kinds_h):
                pill_kind = kinds_h[row_spec.cand_idx]
                disp = (
                    disps_h[row_spec.cand_idx]
                    if row_spec.cand_idx < len(disps_h)
                    else None
                )
            else:
                pill_kind = "new" if row_spec.kind == "insert" else "unchanged"
                disp = None
            pill_host = ft.Container(
                content=self._make_compare_pill_row(pill_kind, disp),
                width=COMPARE_PILL_COL_W,
                alignment=ft.Alignment.TOP_CENTER,
                padding=ft.padding.only(top=4),
            )

            # Left cell: empty placeholder for inserts; struck-through old paragraph
            # (full strikethrough on delete since new_text="") otherwise.
            if row_spec.kind == "insert":
                left_diff = ft.Text(
                    "",
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
            else:
                left_diff = ft.Text(
                    spans=self._future_old_side_spans(row_spec.old_text, row_spec.new_text),
                    style=para_style,
                    selectable=True,
                    expand=True,
                    no_wrap=False,
                )
            self._future_left_diff_texts.append(left_diff)
            left_cell = ft.Container(
                content=left_diff,
                expand=1,
                padding=_COMPARE_HISTORY_CELL_PAD,
            )

            # Right cell: editable AI proposal for replace/insert/equal; empty placeholder
            # for delete rows. The hidden TextField keeps accept/decline index parity.
            if row_spec.kind == "delete":
                right_tf = ft.TextField(
                    **right_tf_kwargs,
                    value="",
                    read_only=True,
                    visible=False,
                )
                right_cell = ft.Container(
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                )
            else:
                right_tf = ft.TextField(
                    **right_tf_kwargs,
                    value=row_spec.new_text,
                    read_only=False,
                    enable_interactive_selection=True,
                    hint_text="…",
                    expand=True,
                    on_change=lambda _e, ix=i: self._on_compare_para_field_change(ix),
                )
                right_cell = ft.Container(
                    content=right_tf,
                    expand=1,
                    padding=_COMPARE_HISTORY_CELL_PAD,
                )

            if show_actions:
                actions_ctrl, hover_wrap_future = self._build_actions_square(i, persistent=False)
            else:
                actions_ctrl, hover_wrap_future = None, None

            eval_host = self._build_eval_cell(i)

            row_cells: list[ft.Control] = [
                eval_host,
                left_cell,
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
            self._future_row_pill_hosts.append(pill_host)
            self._compare_eval_hosts.append(eval_host)
            self._compare_right_fields.append(right_tf)

        if _ctrl_on_page(self._future_rows_listview):
            self._future_rows_listview.update()

        if self._active_check_id is not None:
            self._refresh_all_eval_cells()

        self._refresh_compare_bulk_buttons()
        self._compare_refine_gen += 1
        self.page.run_task(self._debounced_refine_compare_slots, self._compare_refine_gen)

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
        if self._main_tab_index == TAB_HISTORY:
            cand = self._history_newer_side_text() or ""
            n_rows = len(split_paragraphs(cand))
        else:
            cand = self._compare_editor.value or ""
            current = self.editor.value or ""
            n_rows = len(aligned_review_rows(current, cand))
        if n_rows != len(self._compare_right_fields):
            if self._main_tab_index == TAB_FUTURE:
                self._rebuild_future_paragraph_ui()
            else:
                self._rebuild_compare_paragraph_ui()
            return
        if self._main_tab_index == TAB_FUTURE:
            self._refresh_future_left_diff_spans()
        else:
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
            new_text = self._history_newer_side_text() or ""
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
            candidate = self._history_newer_side_text() or ""
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
        # Update diff spans in both History columns after AI refinement.
        if self._main_tab_index == TAB_HISTORY and (
            len(aligned_lefts) == len(self._compare_left_diff_texts)
            and len(aligned_lefts) == len(self._compare_right_fields)
        ):
            for i, left_txt in enumerate(aligned_lefts):
                right_txt = self._compare_right_fields[i].value or ""
                left_t = self._compare_left_diff_texts[i]
                left_t.spans = self._compare_old_side_spans(left_txt, right_txt)
                if _ctrl_on_page(left_t):
                    left_t.update()
                if i < len(self._compare_right_diff_texts):
                    right_t = self._compare_right_diff_texts[i]
                    right_t.spans = self._compare_new_side_spans(left_txt, right_txt)
                    if _ctrl_on_page(right_t):
                        right_t.update()

    def _on_compare_para_field_change(self, index: int) -> None:
        self._sync_compare_buffer_from_fields()
        # History: right fields track the current draft (editor) → autosave to disk.
        # Future:  right fields are the AI proposal candidate → in-memory only, no autosave.
        on_future = self._main_tab_index == TAB_FUTURE
        cand = (self.editor.value if not on_future else self._compare_editor.value) or ""
        if on_future:
            n_rows = len(aligned_review_rows(self.editor.value or "", cand))
        else:
            n_rows = len(split_paragraphs(cand))
        if n_rows != len(self._compare_right_fields):
            if on_future:
                self._rebuild_future_paragraph_ui()
            else:
                self._rebuild_compare_paragraph_ui()
            self._refresh_title_bar()
            if not on_future:
                self._kick_debounced_autosave()
            return
        self._refresh_title_bar()
        self._compare_diff_gen += 1
        dgen = self._compare_diff_gen
        self.page.run_task(self._debounced_compare_diff, dgen)
        if not on_future:
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
        on_future = self._main_tab_index == TAB_FUTURE
        stable_list = (
            self._future_row_stable_texts if on_future else self._compare_row_stable_texts
        )
        if index < len(stable_list):
            revert = stable_list[index]
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
        on_future = self._main_tab_index == TAB_FUTURE
        stable_list = (
            self._future_row_stable_texts if on_future else self._compare_row_stable_texts
        )
        baseline = self._compare_latest_baseline_text()
        paras = split_paragraphs(baseline or "")
        for i, tf in enumerate(self._compare_right_fields):
            if i < len(stable_list):
                revert = stable_list[i]
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

    def _resolve_vid_after_proposal_persist(
        self, new_vid: int | None, cand_body: str
    ) -> int | None:
        """If persist returned None (SHA dedup), use newest snapshot row matching ``cand_body``."""
        if new_vid is not None or not self.current_path:
            return new_vid
        want_sha = version_storage.content_sha256(cand_body)
        try:
            with session_scope() as s:
                snaps = version_storage.list_snapshots(s, self.current_path.resolve())
            for sn in snaps:
                if sn.content_sha256 == want_sha:
                    return sn.version_id
        except BaseException:
            pass
        return None

    async def _stage_compare_margin_review_async(
        self, idx: int, reply: ft.Text, footer: ft.Row, action_id: str
    ) -> None:
        """Like _stage_ai_candidate_async but replaces within the Compare candidate buffer (already on Compare)."""
        text = _strip_change_topic_preamble(reply.value or "")
        if not text:
            self._snack("Reply is empty.")
            return
        act = prompts.get_margin_action(action_id)
        base = self._compare_editor.value or ""
        cand_body = replace_paragraph_at_index(base, idx, text)
        loc_label = f"paragraph {idx + 1}"
        new_vid: int | None = None
        if self.current_path:
            try:
                with session_scope() as s:
                    new_vid = version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        cand_body,
                        "ai_proposal",
                        display_label=f"{act.label} - {loc_label}" if act else f"AI - {loc_label}",
                    )
            except BaseException:
                pass
        new_vid = self._resolve_vid_after_proposal_persist(new_vid, cand_body)
        self._compare_editor.value = cand_body
        self._compare_candidate_source = "ai_preview"
        self._compare_snapshot_version_id = new_vid
        self._pending_ai_accept_action_id = action_id
        if new_vid is not None:
            self._ai_proposal_action_ids[new_vid] = action_id
            self._latest_ai_proposal_vid = new_vid
        self._loaded_proposal_sha = version_storage.content_sha256(self._compare_editor.value or "")
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        self._refresh_tab_toolbar()
        self._refresh_compare_tab_candidate_ui()
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

        reply = ft.Text("", size=12, selectable=True, color=config.ON_SURFACE)
        footer = ft.Row(spacing=8, visible=False)
        bubble = ft.Column([reply, footer], tight=True, spacing=8)
        wrap = ft.Container(
            content=bubble,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            bgcolor=ft.Colors.with_opacity(0.14, config.OUTLINE),
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
                    icon_color=config.ON_SURFACE_VARIANT,
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
                    icon_color=config.ON_SURFACE_VARIANT,
                    tooltip="Review: stage this text as the Compare candidate for this paragraph",
                    style=action_rail_icon_button_style(),
                    on_click=lambda _e, i=idx, r=reply, f=footer, aid=action_id: self.page.run_task(
                        self._stage_compare_margin_review_async, i, r, f, aid
                    ),
                ),
                ft.IconButton(
                    ft.Icons.CLOSE_ROUNDED,
                    icon_size=ACTION_RAIL_ICON_SIZE,
                    icon_color=config.ON_SURFACE_VARIANT,
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
                    icon_color=config.ON_SURFACE_VARIANT,
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
        base = self.editor.value or ""
        cand_body = replace_paragraph_at_index(base, idx, text)
        loc_label = f"paragraph {idx + 1}"
        new_vid: int | None = None
        if self.current_path:
            try:
                with session_scope() as s:
                    new_vid = version_storage.persist_version_snapshot(
                        s,
                        self.current_path.resolve(),
                        cand_body,
                        "ai_proposal",
                        display_label=f"{act.label} - {loc_label}" if act else f"AI - {loc_label}",
                    )
            except BaseException:
                pass
        new_vid = self._resolve_vid_after_proposal_persist(new_vid, cand_body)
        self._compare_editor.value = cand_body
        self._compare_candidate_source = "ai_preview"
        self._compare_snapshot_version_id = new_vid
        self._pending_ai_accept_action_id = action_id
        if new_vid is not None:
            self._ai_proposal_action_ids[new_vid] = action_id
            self._latest_ai_proposal_vid = new_vid
        self._loaded_proposal_sha = version_storage.content_sha256(self._compare_editor.value or "")
        self._hide_prompt_footer(footer)
        self._margin_gen += 1
        await self._debounced_compose_rebuild(self._margin_gen)
        # Set state before flipping the tab widget so _sync_tab_switch_async sees
        # the staged proposal immediately and doesn't reset _compare_editor.
        self._main_tab_index = TAB_FUTURE
        self._main_tabs.selected_index = TAB_FUTURE
        if _ctrl_on_page(self._main_tabs):
            self._main_tabs.update()
        self._rebuild_future_paragraph_ui()
        self._refresh_tab_toolbar()
        self._refresh_compare_tab_candidate_ui()
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._refresh_compare_diff_immediate()
        self._refresh_title_bar()

