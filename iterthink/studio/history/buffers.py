"""Compare buffer helpers, snapshot dropdown rows, and baseline / sync state."""

from __future__ import annotations

from typing import NamedTuple

import flet as ft

from iterthink import prompts
from iterthink.persistence import version_storage
from iterthink.persistence.version_storage import SnapshotInfo
from iterthink.compare.margin import join_paragraphs

from ..constants import (
    REVIEW_MANUAL_CANDIDATE_ACTION_ID,
    REVIEW_SPELL_CANDIDATE_ACTION_ID,
    TAB_FUTURE,
    TAB_HISTORY,
)
from .candidate_state import CompareCandidateSource


def review_action_apply_label(action_id: str) -> str:
    """Display label for accept/flush snapshots when prompts have no margin action for this id."""
    if not action_id:
        return "AI proposal"
    act = prompts.get_margin_action(action_id)
    if act:
        return act.label
    if action_id == REVIEW_MANUAL_CANDIDATE_ACTION_ID:
        return "Manual candidate"
    if action_id == REVIEW_SPELL_CANDIDATE_ACTION_ID:
        return "Spelling suggestions"
    return action_id


class CompareBuffers(NamedTuple):
    """The (baseline, candidate) text pair for the currently-active compare tab.

    baseline — the reference / older side.
    candidate — the target / newer side (draft, AI proposal, or snapshot).

    Use ``MarkdownStudioCompareText._active_compare_buffers()`` to obtain the
    correct pair for whatever tab the user is on, instead of repeating the
    ``if TAB_FUTURE / elif TAB_HISTORY / else`` pattern inline.
    """

    baseline: str
    candidate: str


def history_compare_snapshots(snaps: list[SnapshotInfo]) -> list[SnapshotInfo]:
    return [s for s in snaps if s.reason != "ai_proposal"]


def snapshots_strictly_older_than(
    newest_first: list[SnapshotInfo], newer_version_id: int | None
) -> list[SnapshotInfo]:
    if newer_version_id is None:
        return list(newest_first)
    for i, s in enumerate(newest_first):
        if s.version_id == newer_version_id:
            return newest_first[i + 1 :]
    return list(newest_first)


def build_history_snapshot_dropdown_options(
    slist: list[SnapshotInfo],
    option_style: ft.ButtonStyle | None,
) -> list[ft.dropdown.Option]:
    """Newest-first by wall time so imports are not listed after older autosaves."""
    ordered = sorted(slist, key=lambda sn: (sn.created_at, sn.version_id), reverse=True)
    out: list[ft.dropdown.Option] = []
    for sn in ordered:
        row_text = version_storage.snapshot_dropdown_text(sn)
        if version_storage.snapshot_bucket(sn) == "import":
            out.append(
                ft.dropdown.Option(
                    key=str(sn.version_id),
                    text=f"Import - {row_text}",
                    style=option_style,
                )
            )
        else:
            out.append(
                ft.dropdown.Option(
                    key=str(sn.version_id),
                    text=row_text,
                    style=option_style,
                )
            )
    return out


class _HistoryBuffersMixin:
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

    def _reset_compare_state(self) -> None:
        """Clear all compare-side state before loading a new document.

        Centralises the field assignments that open_file used to make inline.
        Keeps the full set of compare fields in one documented place so that
        adding a new format (e.g. IFC) only requires touching this method.
        """
        self._compare_candidate_source = CompareCandidateSource.DRAFT
        self._compare_snapshot_version_id = None
        self._compare_newer_version_id = None
        self._compare_newer_cached_body = ""
        self._pending_ai_accept_action_id = None
        self._compare_pdf_peer_snapshot_id = None
        self._latest_ai_proposal_vid = None
        self._ai_proposal_action_ids.clear()
        self._loaded_proposal_sha = None
        self._pending_post_import_history_vid = None
        clear_spell = getattr(self, "_clear_spell_suggest_cache", None)
        if clear_spell is not None:
            clear_spell()

    def _active_compare_buffers(self) -> CompareBuffers:
        """Return the (baseline, candidate) text pair for the active tab.

        Centralises the ``if TAB_FUTURE / elif TAB_HISTORY / else`` lookup
        that was duplicated across checks, pill refresh, and slot refinement.

        - History:  baseline = snapshot (left column)
                    candidate = newer draft or "Current draft" (right column)
        - Future:   baseline = current editor draft
                    candidate = AI proposal
        - Present:  baseline = latest saved baseline snapshot
                    candidate = compare editor buffer
        """
        if self._main_tab_index == TAB_FUTURE:
            return CompareBuffers(
                baseline=self.editor.value or "",
                candidate=self._compare_editor.value or "",
            )
        if self._main_tab_index == TAB_HISTORY:
            return CompareBuffers(
                baseline=self._compare_editor.value or "",
                candidate=self._history_newer_side_text() or "",
            )
        return CompareBuffers(
            baseline=self._compare_latest_baseline_text(),
            candidate=self._compare_editor.value or "",
        )

    def _sync_compare_buffer_from_fields(self) -> None:
        parts = [tf.value or "" for tf in self._compare_right_fields]
        if self._main_tab_index == TAB_FUTURE:
            kinds = getattr(self, "_future_row_kinds", None) or []
            cand_idxs = getattr(self, "_future_row_cand_idx", None) or []
            by_new: dict[int, str] = {}
            for i, tf in enumerate(self._compare_right_fields):
                if i < len(kinds) and kinds[i] == "delete":
                    continue
                if i < len(cand_idxs) and cand_idxs[i] is not None and cand_idxs[i] >= 0:
                    by_new[int(cand_idxs[i])] = tf.value or ""
            if not by_new:
                merged = ""
            else:
                merged = join_paragraphs([by_new.get(j, "") for j in range(max(by_new) + 1)])
        else:
            merged = "\n\n".join(parts) if parts else ""
        if self._main_tab_index == TAB_HISTORY:
            if self._compare_newer_version_id is None:
                self.editor.value = merged
                self._editor_prev_for_list_continue = merged
            else:
                self._compare_newer_cached_body = merged
        else:
            self._compare_editor.value = merged

    def _future_review_candidate_para_count_mismatch(self, n_candidate_paras: int) -> bool:
        """True when the merged candidate buffer's paragraph count no longer matches the Review grid.

        Review rows include pure ``delete`` slots (no candidate paragraph). Comparing
        ``len(split_paragraphs(cand))`` to ``len(_compare_right_fields)`` therefore
        spuriously mismatches whenever deletes exist, forcing a full rebuild on every
        keystroke and wiping edits such as trailing spaces.
        """
        kinds = getattr(self, "_future_row_kinds", None) or []
        n_fields = len(self._compare_right_fields)
        if not kinds or len(kinds) != n_fields:
            return n_candidate_paras != n_fields
        n_comparison_slots = sum(1 for k in kinds if k != "delete")
        return n_candidate_paras != n_comparison_slots

    def _capture_compare_baseline_snapshot(self) -> None:
        """Store Compose buffer as the Compare left baseline (draft mode only uses this via _compare_latest_baseline_text)."""
        self._compare_baseline_snapshot = self.editor.value or ""

    def _compare_latest_baseline_text(self) -> str:
        """On History, the newer (right) side; otherwise the compose document."""
        if self._main_tab_index == TAB_HISTORY:
            return self._history_newer_side_text()
        return self.editor.value or ""
