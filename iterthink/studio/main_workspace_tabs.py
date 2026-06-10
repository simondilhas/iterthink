"""Main workspace tab strip: History / Focus / Review.

The UI uses Material-style pieces wired together explicitly:

- ``ft.Tabs`` supplies ``length``, ``selected_index``, and ``on_change`` so the
  client keeps a single tab index.
- ``content`` is a custom column: ``TabBar``, always-visible filename band,
  History-only version strip, then ``TabBarView`` (bodies). Review subtabs and
  the Current/Candidate chrome live inside the Review page column. Keep
  ``selected_index`` aligned with async switches.

``MarkdownStudio`` builds the controls in ``__init__``; this module holds the
async switch queue and toolbar/subtab reconciliation.
"""

from __future__ import annotations

import asyncio
import logging

import flet as ft

from iterthink import config
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo

from . import ui_theme
from .constants import (
    REVIEW_MANUAL_CANDIDATE_ACTION_ID,
    TAB_FUTURE,
    TAB_HISTORY,
    TAB_PRESENT,
)
from .history.candidate_state import CompareCandidateSource
from .util import ctrl_on_page as _ctrl_on_page

_log = logging.getLogger(__name__)


class MainWorkspaceTabsMixin:
    """Tab index, async switch worker, toolbar swap, Review subtabs, tab bar theme."""

    def _init_main_workspace_tab_fields(self) -> None:
        self._main_tab_index = TAB_PRESENT
        self._tab_switch_lock = asyncio.Lock()
        self._tab_switch_seq = 0
        self._tab_switch_requested = None
        self._tab_switch_worker_running = False
        self._review_subtab_index = 0
        self._compare_tab_bar_hover_index1 = False

    def _is_tab_switch_stale(self, switch_seq: int) -> bool:
        return switch_seq != self._tab_switch_seq

    def _queue_tab_switch(self, tab_index: int) -> int:
        self._tab_switch_seq += 1
        self._tab_switch_requested = tab_index
        return self._tab_switch_seq

    def _request_tab_switch(self, tab_index: int) -> None:
        self._queue_tab_switch(tab_index)
        if self._tab_switch_worker_running:
            return
        self._tab_switch_worker_running = True
        self.page.run_task(self._tab_switch_worker_async)

    async def _request_tab_switch_async(self, tab_index: int) -> None:
        switch_seq = self._queue_tab_switch(tab_index)
        async with self._tab_switch_lock:
            if self._is_tab_switch_stale(switch_seq):
                return
            if self._tab_switch_requested is None and self._main_tab_index == tab_index:
                self._apply_active_tab_ui_state()
                return
            if self._tab_switch_requested == tab_index:
                self._tab_switch_requested = None
            await self._sync_tab_switch_async(tab_index, switch_seq)

    async def _tab_switch_worker_async(self) -> None:
        try:
            async with self._tab_switch_lock:
                while self._tab_switch_requested is not None:
                    new_ix = self._tab_switch_requested
                    switch_seq = self._tab_switch_seq
                    self._tab_switch_requested = None
                    await self._sync_tab_switch_async(new_ix, switch_seq)
        finally:
            self._tab_switch_worker_running = False
            if self._tab_switch_requested is not None:
                self._request_tab_switch(self._tab_switch_requested)

    def _on_main_tabs_change(self, e: ft.ControlEvent) -> None:
        try:
            new_ix = int(e.data)
        except (TypeError, ValueError):
            new_ix = int(self._main_tabs.selected_index)
        self._request_tab_switch(new_ix)

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
        if not config.RAG_SYSTEM:
            return
        if idx == self._review_subtab_index:
            return
        self._review_subtab_index = idx
        if idx == 1 and hasattr(self, "_ensure_impact_tab_initialized"):
            self._ensure_impact_tab_initialized()
        self._apply_active_tab_ui_state()
        if self._main_tab_index == TAB_FUTURE and hasattr(self, "_sync_future_pdf_layers_visibility"):
            self._sync_future_pdf_layers_visibility()
        # Impact → Difference: visibility alone does not always relayout the ListView; rebuild once.
        if self._main_tab_index == TAB_FUTURE and idx == 0:
            self._refresh_compare_diff_immediate()
        self.page.update()

    def _discard_future_tab_loading_spinner(self) -> None:
        """If we bailed out of a Review tab switch after showing the placeholder ring, clear it."""
        ctrl = self._future_rows_listview.controls
        if len(ctrl) != 1 or not isinstance(ctrl[0], ft.Container):
            return
        inner = ctrl[0].content
        if isinstance(inner, ft.ProgressRing):
            ctrl.clear()
            if _ctrl_on_page(self._future_rows_listview):
                self._future_rows_listview.update()

    def _apply_active_tab_ui_state(self) -> None:
        if not config.RAG_SYSTEM and self._review_subtab_index != 0:
            self._review_subtab_index = 0
        self._main_tabs.selected_index = self._main_tab_index
        if _ctrl_on_page(self._main_tabs):
            self._main_tabs.update()
        on_review = self._main_tab_index == TAB_FUTURE
        if config.RAG_SYSTEM:
            diff_active = on_review and self._review_subtab_index == 0
            impact_active = on_review and self._review_subtab_index == 1
        else:
            diff_active = on_review
            impact_active = False
        self._review_change_panel.visible = diff_active
        self._review_change_panel.expand = diff_active
        self._review_impact_panel.visible = impact_active
        self._review_impact_panel.expand = impact_active
        if _ctrl_on_page(self._review_change_panel):
            self._review_change_panel.update()
        if _ctrl_on_page(self._review_impact_panel):
            self._review_impact_panel.update()
        # Re-render Impact tab when switching into the Impact subtab.
        if impact_active:
            pid = getattr(self, "_active_impact_prompt_id", None)
            if pid and hasattr(self, "_refresh_impact_annotations_ui"):
                self._refresh_impact_annotations_ui(str(pid))
            elif not pid and hasattr(self, "_populate_impact_para_placeholders"):
                self._populate_impact_para_placeholders()
        sub_col = getattr(self, "_review_subpanels_column", None)
        if sub_col is not None and _ctrl_on_page(sub_col):
            sub_col.update()
        self._refresh_tab_toolbar()
        self._apply_focus_preview_mode()
        if hasattr(self, "_sync_plan_filename_chrome"):
            self._sync_plan_filename_chrome()
        if hasattr(self, "_sync_ki_topic_strip_after_workspace_tab_change"):
            self._sync_ki_topic_strip_after_workspace_tab_change()

    def _refresh_review_subtab_strip(self) -> None:
        """Show/hide and restyle the Difference|Impact strip from ``rag_system``."""
        strip = getattr(self, "_review_subtab_strip", None)
        if strip is not None:
            want_strip = config.RAG_SYSTEM
            if strip.visible != want_strip:
                strip.visible = want_strip
                if _ctrl_on_page(strip):
                    strip.update()
        if not config.RAG_SYSTEM:
            return
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

    def _refresh_tab_toolbar(self) -> None:
        """History version strip; Review Current/Candidate row visibility; restyle subtab pills."""
        self._refresh_review_subtab_strip()
        on_hist = self._main_tab_index == TAB_HISTORY
        new_tool = (
            self._toolbar_history
            if on_hist
            else (
                self._toolbar_present_spacer
                if self._main_tab_index == TAB_PRESENT
                else self._toolbar_review_spacer
            )
        )
        # History toolbar stacks Older/Newer + plan PDF row; height follows content.
        tb_h = None if on_hist else 0
        tb_vis = on_hist
        inner_h = self._tab_toolbar_inner.height
        if tb_h is None:
            height_changed = inner_h is not None
        else:
            height_changed = (inner_h or 0) != tb_h
        tb_changed = (
            self._tab_toolbar_inner.content is not new_tool
            or height_changed
            or self._tab_toolbar.visible != tb_vis
        )
        self._tab_toolbar_inner.content = new_tool
        self._tab_toolbar_inner.height = tb_h
        self._tab_toolbar.visible = tb_vis
        if tb_changed:
            if _ctrl_on_page(self._tab_toolbar_inner):
                self._tab_toolbar_inner.update()
            if _ctrl_on_page(self._tab_toolbar):
                self._tab_toolbar.update()

        on_diff = self._main_tab_index == TAB_FUTURE and self._review_subtab_index == 0
        show_plan_pdf = (
            on_diff
            and getattr(self, "_compare_candidate_source", None) == "pdf_original"
            and hasattr(self, "_is_plan_pdf_compare")
            and self._is_plan_pdf_compare()
        )
        self._review_difference_chrome_row.visible = on_diff and not show_plan_pdf
        if _ctrl_on_page(self._review_difference_chrome_row):
            self._review_difference_chrome_row.update()

        # Dropdown menus render in Flet's overlay layer, independent of the widget tree.
        # They must be hidden/disabled explicitly so inactive menus cannot linger.
        on_history = self._main_tab_index == TAB_HISTORY
        on_review = self._main_tab_index == TAB_FUTURE and self._review_subtab_index == 0
        text_single = (
            on_review
            and hasattr(self, "_review_text_single_mode")
            and self._review_text_single_mode()
        )
        lack_older = on_history and not bool(self._compare_candidate_dropdown.options)
        asset_compare = on_history and self._compare_candidate_source in ("pdf_original", "ifc_original")
        md_visible = on_history and not asset_compare
        if hasattr(self, "_toolbar_history_md_row"):
            self._toolbar_history_md_row.visible = md_visible
        self._compare_candidate_dropdown.visible = md_visible
        self._compare_candidate_dropdown.disabled = (
            not md_visible or self.current_path is None or lack_older
        )
        self._compare_newer_dropdown.visible = md_visible
        self._compare_newer_dropdown.disabled = not md_visible or self.current_path is None
        self._review_baseline_dropdown.visible = on_review and not text_single
        self._review_baseline_dropdown.disabled = (
            not on_review
            or text_single
            or self.current_path is None
            or not bool(self._review_baseline_dropdown.options)
        )
        self._review_candidate_dropdown.visible = on_review
        self._review_candidate_dropdown.disabled = not on_review or not bool(
            self._review_candidate_dropdown.options
        )
        if _ctrl_on_page(self._compare_candidate_dropdown):
            self._compare_candidate_dropdown.update()
        if _ctrl_on_page(self._compare_newer_dropdown):
            self._compare_newer_dropdown.update()
        if hasattr(self, "_toolbar_history_md_row") and _ctrl_on_page(self._toolbar_history_md_row):
            self._toolbar_history_md_row.update()
        if hasattr(self, "_sync_review_text_layout_chrome"):
            self._sync_review_text_layout_chrome()
        if _ctrl_on_page(self._review_baseline_dropdown):
            self._review_baseline_dropdown.update()
        if _ctrl_on_page(self._review_candidate_dropdown):
            self._review_candidate_dropdown.update()

    def _on_main_tab_bar_hover(self, e: ft.TabBarHoverEvent) -> None:
        self._compare_tab_bar_hover_index1 = e.index == TAB_HISTORY and e.hovering

    def _apply_main_workspace_tab_chrome_theme(self) -> None:
        """History / Focus / Review strip: colors follow ``config`` / ``ui_theme``."""
        self._main_tab_bar.indicator_color = config.HIGHLIGHT
        self._main_tab_bar.divider_color = ui_theme.outline_muted(alpha=0.28)
        self._main_tab_bar.label_color = config.ON_SURFACE
        self._main_tab_bar.unselected_label_color = config.ON_SURFACE_VARIANT
        self._main_tab_bar.overlay_color = ft.Colors.with_opacity(0.06, config.ON_SURFACE)
        self._sticky_tab_header.bgcolor = config.SURFACE
        self._tab_toolbar.bgcolor = config.SURFACE
        self._workspace_filename_band.bgcolor = config.SURFACE
        self._review_difference_chrome_row.bgcolor = config.SURFACE
        if _ctrl_on_page(self._main_tab_bar):
            self._main_tab_bar.update()
        if _ctrl_on_page(self._tab_toolbar):
            self._tab_toolbar.update()
        if _ctrl_on_page(self._workspace_filename_band):
            self._workspace_filename_band.update()
        if _ctrl_on_page(self._review_difference_chrome_row):
            self._review_difference_chrome_row.update()

    async def _sync_tab_switch_async(self, new_ix: int, switch_seq: int | None = None) -> None:
        if switch_seq is None:
            switch_seq = self._tab_switch_seq
        prev = self._main_tab_index
        if new_ix == prev:
            self._apply_active_tab_ui_state()
            return

        self._cancel_autosave_timers()
        if self.current_path and self._is_dirty():
            await self.save_file(silent=True, snapshot_reason="pre_switch")
            if self._is_tab_switch_stale(switch_seq):
                return
        # Persist any in-flight Review proposal edits before switching away from Future.
        if prev == TAB_FUTURE:
            self._flush_review_edits_if_changed()

        # Leaving Present: nothing to flush (editor is the source of truth).
        # Leaving History: right fields are read-only carriers; current draft is editor.value.
        # Leaving Future: right fields are the AI proposal candidate, kept in _compare_editor.value
        #   in-memory; nothing to persist until Accept.

        # Set the active tab index now so all rebuild helpers (eval cell width, diff spans,
        # pill paths) read the correct tab when they check _main_tab_index during construction.
        self._main_tab_index = new_ix

        # Entering History (TAB_HISTORY): prefer 2nd-newest history autosave (newest ≈ draft), else rebuild.
        if new_ix == TAB_HISTORY:
            if prev != TAB_HISTORY:
                self._compare_newer_version_id = None
                self._compare_newer_cached_body = ""
            plan_pdf_history = (
                self.current_path is not None
                and hasattr(self, "_document_pdf_profile")
                and self._document_pdf_profile() == "plan"
                and hasattr(self, "_ensure_plan_pdf_compare_active")
                and self._ensure_plan_pdf_compare_active()
            )
            if plan_pdf_history:
                self._rebuild_compare_view()
            else:
                pick_vid: int | None = None
                pending_post_import = self._pending_post_import_history_vid
                if pending_post_import is not None:
                    self._pending_post_import_history_vid = None
                elif self.current_path and prev != TAB_HISTORY:
                    with session_scope() as s:
                        snaps = content_repo.list_snapshots(s, self.current_path.resolve())
                    pick_vid = content_repo.second_newest_history_autosave_version_id(snaps)

                if pick_vid is not None:
                    self._select_snapshot_as_candidate(pick_vid)
                    self._capture_compare_baseline_snapshot()
                else:
                    if self._compare_candidate_source not in (
                        "snapshot",
                        "pdf_original",
                        "docx_original",
                        "ifc_original",
                    ):
                        # No snapshot selected yet; prime left from draft until user picks a version.
                        self._compare_candidate_source = "snapshot"
                        self._compare_editor.value = self.editor.value or ""
                        self._capture_compare_baseline_snapshot()
                    self._rebuild_compare_view()

        # Entering Present (TAB_PRESENT)
        elif new_ix == TAB_PRESENT:
            self._margin_gen += 1
            await self._debounced_compose_rebuild(self._margin_gen)
            if not getattr(self, "_skip_compose_plan_refresh_on_tab", False):
                await self._refresh_compose_plan_surface_async()
            if self._is_tab_switch_stale(switch_seq):
                self._main_tab_index = prev
                self._apply_active_tab_ui_state()
                return

        # Entering Future (TAB_FUTURE): auto-load the most recent ai_proposal / legacy ai_staged when nothing is staged.
        elif new_ix == TAB_FUTURE:
            # Tab bar + Review chrome were still on the previous tab until now, so DB/list work looked frozen.
            self._apply_active_tab_ui_state()
            self._future_rows_listview.controls.clear()
            self._future_rows_listview.controls.append(
                ft.Container(
                    content=ft.ProgressRing(
                        width=24, height=24, stroke_width=2, color=config.PRIMARY_COLOR
                    ),
                    alignment=ft.Alignment.CENTER,
                    expand=True,
                    padding=ft.padding.only(top=48),
                )
            )
            if _ctrl_on_page(self._future_rows_listview):
                self._future_rows_listview.update()
            await asyncio.sleep(0)  # yield so the client paints Review + spinner before snapshot IO

            try:
                if (
                    self.current_path is not None
                    and hasattr(self, "_document_pdf_profile")
                    and self._document_pdf_profile() == "plan"
                    and hasattr(self, "_ensure_plan_pdf_compare_active")
                ):
                    self._ensure_plan_pdf_compare_active()
                spell_review_hold = (
                    self._compare_candidate_source == CompareCandidateSource.SPELL_PREVIEW
                )
                already_staged = spell_review_hold or (
                    self._compare_candidate_source == CompareCandidateSource.AI_PREVIEW
                    and self._pending_ai_accept_action_id
                    and self._compare_snapshot_version_id is not None
                )
                pdf_import_review = (
                    self._compare_candidate_source == CompareCandidateSource.PDF_ORIGINAL
                )
                if not already_staged and not pdf_import_review:
                    target_vid = self._latest_ai_proposal_vid
                    if target_vid is None and self.current_path:
                        with session_scope() as s:
                            snaps = content_repo.list_snapshots(s, self.current_path.resolve())
                        for sn in snaps:  # newest first
                            if sn.reason in ("ai_proposal", "ai_staged", "review_edit"):
                                target_vid = sn.version_id
                                break
                    if target_vid is not None:
                        self._select_proposal_as_review_candidate(target_vid)
                        self._latest_ai_proposal_vid = target_vid
                    else:
                        # No proposals: mirror compose into the candidate so rows are editable (equal/replace),
                        # and set a synthetic action id so Accept / approve-all still write disk + snapshots.
                        seeded = self.editor.value or ""
                        self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
                        self._compare_editor.value = seeded
                        self._pending_ai_accept_action_id = REVIEW_MANUAL_CANDIDATE_ACTION_ID
                        self._compare_snapshot_version_id = None
                        self._loaded_proposal_sha = content_repo.content_sha256(seeded)
                if self._is_tab_switch_stale(switch_seq):
                    self._discard_future_tab_loading_spinner()
                    self._main_tab_index = prev
                    self._apply_active_tab_ui_state()
                    return
                if self._compare_candidate_source == CompareCandidateSource.SPELL_PREVIEW:
                    await self._sync_spell_candidate_for_review_tab_async()
                if hasattr(self, "_ensure_text_review_compare_layout_default"):
                    self._ensure_text_review_compare_layout_default()
                self._rebuild_future_paragraph_ui()
                if hasattr(self, "_sync_future_pdf_layers_visibility"):
                    self._sync_future_pdf_layers_visibility()
                if (
                    self._compare_candidate_source == CompareCandidateSource.PDF_ORIGINAL
                    and hasattr(self, "_is_plan_pdf_compare")
                    and self._is_plan_pdf_compare()
                    and hasattr(self, "_refresh_plan_compare_bar")
                ):
                    self._refresh_plan_compare_bar()
            except BaseException as ex:
                _log.exception("Review tab: failed while loading snapshot or building rows")
                self._discard_future_tab_loading_spinner()
                if hasattr(self, "_future_review_load_failed_ui"):
                    self._future_review_load_failed_ui(ex)

        if self._is_tab_switch_stale(switch_seq):
            self._main_tab_index = prev
            self._apply_active_tab_ui_state()
            return
        self._hide_all_result_card_overlays()
        if new_ix != TAB_HISTORY:
            self._compare_version_dd_focused = False
            self._compare_dropdown_hover = False
            self._compare_newer_dropdown_hover = False
        if new_ix == TAB_PRESENT:
            self._compare_tab_bar_hover_index1 = False
        # Populate History/Review dropdown options before chrome (disabled state depends on options).
        self._refresh_compare_tab_candidate_ui()
        self._apply_compare_candidate_dropdown_tab_chrome()
        if new_ix == TAB_HISTORY:
            self._refresh_plan_compare_bar()
        self._apply_active_tab_ui_state()
        self._refresh_title_bar()
