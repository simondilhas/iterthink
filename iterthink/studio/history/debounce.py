"""Debounced compare diff refresh, pill refresh, and LLM slot refinement."""

from __future__ import annotations

import asyncio

from iterthink.compare import paragraph_compare
from iterthink.compare.margin import split_paragraphs

from ..constants import TAB_FUTURE, TAB_HISTORY
from ..util import ctrl_on_page as _ctrl_on_page
from .candidate_state import CompareCandidateSource


class _HistoryDebounceMixin:
    def _refresh_compare_diff_immediate(self) -> None:
        if self._main_tab_index == TAB_FUTURE:
            self._rebuild_future_paragraph_ui()
            return
        self._rebuild_compare_view()

    async def _debounced_compare_diff(self, gen: int) -> None:
        await asyncio.sleep(0.12)
        if gen != self._compare_diff_gen:
            return
        if self._compare_candidate_source not in (
            CompareCandidateSource.SNAPSHOT,
            CompareCandidateSource.DRAFT,
            CompareCandidateSource.AI_PREVIEW,
            CompareCandidateSource.SPELL_PREVIEW,
        ):
            return  # non-text format (PDF, DOCX, IFC, …); no debounced diff needed
        if self._main_tab_index == TAB_HISTORY:
            cand = self._history_newer_side_text() or ""
            n_rows = len(split_paragraphs(cand))
        else:
            cand = self._compare_editor.value or ""
            n_rows = len(split_paragraphs(cand))
        need_rebuild = (
            self._future_review_candidate_para_count_mismatch(n_rows)
            if self._main_tab_index == TAB_FUTURE
            else n_rows != len(self._compare_right_fields)
        )
        if need_rebuild:
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
        buffers = self._active_compare_buffers()
        kinds, disps = paragraph_compare.compare_slots_heuristic(buffers.baseline, buffers.candidate)
        # History + Review: no stacked "Moved" chip on comparison rows (ghost rows + arrow only);
        # a second pill column was taller than single-pill rows and broke column alignment on Review.
        for i, host in enumerate(self._active_pill_hosts()):
            k = kinds[i] if i < len(kinds) else "stable"
            d = disps[i] if i < len(disps) else None
            host.content = self._make_compare_pill_row(k, d, show_moved_badge=False)
            if _ctrl_on_page(host):
                host.update()

    async def _debounced_refine_compare_slots(self, gen: int) -> None:
        await asyncio.sleep(0.05)
        if gen != self._compare_refine_gen:
            return
        if self._compare_candidate_source == CompareCandidateSource.SPELL_PREVIEW:
            return
        if self._main_tab_index not in (TAB_HISTORY, TAB_FUTURE):
            return
        buffers = self._active_compare_buffers()
        if not buffers.baseline.strip() and not buffers.candidate.strip():
            return
        try:
            refined, aligned_lefts, disps_ref = await paragraph_compare.classify_slots_async(
                self._db,
                self._make_llm_backend(),
                chat_model=self.chat_model_for_requests(),
                doc_path=str(self.current_path.resolve()) if self.current_path else None,
                baseline_text=buffers.baseline,
                new_text=buffers.candidate,
            )
        except BaseException:
            return
        if gen != self._compare_refine_gen:
            return
        for i, host in enumerate(self._active_pill_hosts()):
            k = refined[i] if i < len(refined) else "stable"
            d = disps_ref[i] if i < len(disps_ref) else None
            host.content = self._make_compare_pill_row(k, d, show_moved_badge=False)
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
        n_rows = len(split_paragraphs(cand))
        need_rebuild = (
            self._future_review_candidate_para_count_mismatch(n_rows)
            if on_future
            else n_rows != len(self._compare_right_fields)
        )
        if need_rebuild:
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
