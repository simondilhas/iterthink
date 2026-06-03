"""Bulk compare buttons, per-paragraph accept/decline, and AI-accept snapshot persistence."""

from __future__ import annotations

from iterthink.compare.margin import (
    apply_review_insert,
    join_paragraphs,
    remove_paragraph_at_index,
    replace_paragraph_at_index,
    split_paragraphs,
)
from iterthink.db.session import session_scope
from iterthink.persistence import version_storage

from ..constants import (
    REVIEW_MANUAL_CANDIDATE_ACTION_ID,
    REVIEW_SPELL_CANDIDATE_ACTION_ID,
    TAB_FUTURE,
)
from ..util import ctrl_on_page as _ctrl_on_page
from .buffers import review_action_apply_label
from .candidate_state import CompareCandidateSource


class _HistoryBulkActionsMixin:
    def _compare_has_pending_bulk_apply(self) -> bool:
        if not self.current_path or not self._compare_right_fields:
            return False
        # Manual seeded candidate: always allow bulk apply so Approve All stays visible
        # even before the user has made any edits to the right column.
        if self._pending_ai_accept_action_id == REVIEW_MANUAL_CANDIDATE_ACTION_ID:
            return True
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
        apply_label = review_action_apply_label(action_id)
        before_label = "Before accept · all paragraphs" if bulk else f"Before accept · paragraph {para_index + 1}"
        if action_id == REVIEW_SPELL_CANDIDATE_ACTION_ID:
            before_reason = "before_spell_apply"
            after_reason = "spell_apply"
        else:
            before_reason = "before_apply"
            after_reason = "ai_apply"
        try:
            with session_scope() as s:
                rp = self.current_path.resolve()
                version_storage.persist_version_snapshot(
                    s,
                    rp,
                    pre_buf,
                    before_reason,
                    display_label=before_label,
                )
                version_storage.persist_version_snapshot(
                    s,
                    rp,
                    post_buf,
                    after_reason,
                    display_label=apply_label,
                )
        except BaseException:
            pass

    async def _compare_accept_paragraph_async(self, index: int) -> None:
        if not self.current_path:
            self._snack("Open a note first.")
            return
        if index < 0 or index >= len(self._compare_right_fields):
            return
        cand_para = self._compare_right_fields[index].value or ""
        pre_buf = self.editor.value or ""
        on_future = self._main_tab_index == TAB_FUTURE
        para_index_for_persist = index
        snack_msg = f"Paragraph {index + 1} applied to the document."
        if on_future and index < len(self._future_row_kinds):
            kind = self._future_row_kinds[index]
            oi = (
                self._future_row_old_index[index]
                if index < len(self._future_row_old_index)
                else index
            )
            ia = (
                self._future_row_insert_after_old[index]
                if index < len(self._future_row_insert_after_old)
                else -1
            )
            n_paras = len(split_paragraphs(pre_buf))
            if kind == "delete":
                if oi < 0 or oi >= n_paras:
                    self._snack("Could not map this row to the document.")
                    return
                new_buf = remove_paragraph_at_index(pre_buf, oi)
                para_index_for_persist = oi
                snack_msg = f"Paragraph {oi + 1} removed from the document."
            elif kind == "insert":
                new_buf = apply_review_insert(pre_buf, ia, cand_para)
                para_index_for_persist = index
                snack_msg = "Applied to the document."
            elif kind in ("replace", "equal"):
                if oi < 0 or oi >= n_paras:
                    self._snack("Could not map this row to the document.")
                    return
                new_buf = replace_paragraph_at_index(pre_buf, oi, cand_para)
                para_index_for_persist = oi
                snack_msg = f"Paragraph {oi + 1} applied to the document."
            else:
                new_buf = replace_paragraph_at_index(pre_buf, index, cand_para)
        else:
            new_buf = replace_paragraph_at_index(pre_buf, index, cand_para)
        review_apply = self._compare_candidate_source in (
            CompareCandidateSource.AI_PREVIEW,
            CompareCandidateSource.SPELL_PREVIEW,
        ) and bool(self._pending_ai_accept_action_id)
        if review_apply:
            self._persist_ai_accept_snapshots(
                pre_buf,
                new_buf,
                para_index=para_index_for_persist,
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
        self._snack(snack_msg)

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
        pre_buf = self.editor.value or ""
        on_future = self._main_tab_index == TAB_FUTURE
        if on_future:
            kinds = getattr(self, "_future_row_kinds", None) or []
            cidxs = getattr(self, "_future_row_cand_idx", None) or []
            by_new: dict[int, str] = {}
            for i, tf in enumerate(self._compare_right_fields):
                if i < len(kinds) and kinds[i] == "delete":
                    continue
                if i < len(cidxs) and cidxs[i] is not None and cidxs[i] >= 0:
                    by_new[int(cidxs[i])] = tf.value or ""
            new_buf = (
                join_paragraphs([by_new.get(j, "") for j in range(max(by_new) + 1)])
                if by_new
                else ""
            )
        else:
            parts = [tf.value or "" for tf in self._compare_right_fields]
            new_buf = "\n\n".join(parts)
        review_apply = self._compare_candidate_source in (
            CompareCandidateSource.AI_PREVIEW,
            CompareCandidateSource.SPELL_PREVIEW,
        ) and bool(self._pending_ai_accept_action_id)
        if review_apply:
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
