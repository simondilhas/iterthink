"""Debounced spell suggestion cache for Review SPELL_PREVIEW mode."""

from __future__ import annotations

import asyncio

from iterthink.persistence import version_storage

from ..constants import REVIEW_KEY_SPELL_CHECK, REVIEW_SPELL_CANDIDATE_ACTION_ID, TAB_FUTURE, TAB_PRESENT
from ..util import ctrl_on_page as _ctrl_on_page
from .candidate_state import CompareCandidateSource
from .spell_suggest import spellchecker_available, suggest_spell_corrected_text


class _HistorySpellReviewMixin:
    """Background full-body spell suggestions; see ``suggest_spell_corrected_text``."""

    def _enter_spell_review_mode(self) -> None:
        """Activate Review spelling candidate (same as choosing ``Draft vs spelling`` in the dropdown)."""
        self._flush_review_edits_if_changed()
        self._reset_check_analysis_session()
        self._compare_candidate_source = CompareCandidateSource.SPELL_PREVIEW
        self._pending_ai_accept_action_id = REVIEW_SPELL_CANDIDATE_ACTION_ID
        self._compare_snapshot_version_id = None
        self._sync_spell_candidate_from_cache()
        self._loaded_proposal_sha = version_storage.content_sha256(
            self._compare_editor.value or ""
        )
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        if self._main_tab_index == TAB_FUTURE:
            dd = getattr(self, "_review_candidate_dropdown", None)
            if dd is not None:
                dd.value = REVIEW_KEY_SPELL_CHECK
                if _ctrl_on_page(dd):
                    dd.update()
            self._refresh_compare_tab_candidate_ui()
            if hasattr(self, "_apply_compare_candidate_dropdown_tab_chrome"):
                self._apply_compare_candidate_dropdown_tab_chrome()
            self._refresh_compare_diff_immediate()
        self._spell_review_snack_if_no_suggestions()
        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()

    def _spell_review_snack_if_no_suggestions(self) -> None:
        if not hasattr(self, "_snack"):
            return
        if not spellchecker_available():
            self._snack(
                "Spelling review needs a working pyspellchecker dictionary "
                "(install the package, copy language files into the store spell_dictionaries folder on startup, "
                "or set a custom dictionary file in Settings)."
            )
            return
        if (self._compare_editor.value or "") == (self.editor.value or ""):
            self._snack("No unknown words found — suggested text matches the draft.")

    async def _debounced_spell_suggest_cache(self, gen: int) -> None:
        await asyncio.sleep(0.32)
        if gen != self._spell_suggest_gen:
            return
        text = self.editor.value or ""
        body = await asyncio.to_thread(suggest_spell_corrected_text, text)
        if gen != self._spell_suggest_gen:
            return
        self._spell_suggest_cached_body = body
        self._spell_suggest_cached_src_sha = version_storage.content_sha256(text)
        # SPELL_PREVIEW: keep candidate aligned with latest suggestion (MVP; overwrites right edits).
        if (
            self._compare_candidate_source == CompareCandidateSource.SPELL_PREVIEW
            and self._main_tab_index == TAB_FUTURE
        ):
            self._compare_editor.value = body
            if _ctrl_on_page(self._compare_editor):
                self._compare_editor.update()
            self._compare_diff_gen += 1
            dgen = self._compare_diff_gen
            self.page.run_task(self._debounced_compare_diff, dgen)

    def _kick_debounced_spell_suggest_cache(self) -> None:
        if not self.current_path:
            return
        self._spell_suggest_gen += 1
        g = self._spell_suggest_gen
        self.page.run_task(self._debounced_spell_suggest_cache, g)

    def _clear_spell_suggest_cache(self) -> None:
        self._spell_suggest_gen += 1
        self._spell_suggest_cached_body = ""
        self._spell_suggest_cached_src_sha = None

    def _editor_sha256(self) -> str:
        return version_storage.content_sha256(self.editor.value or "")

    def _sync_spell_candidate_from_cache(self) -> None:
        """Set ``_compare_editor`` from cache when SHA matches current editor (else sync compute)."""
        want = self._editor_sha256()
        if self._spell_suggest_cached_src_sha == want and self._spell_suggest_cached_body != "":
            self._compare_editor.value = self._spell_suggest_cached_body
        else:
            self._compare_editor.value = suggest_spell_corrected_text(self.editor.value or "")
            self._spell_suggest_cached_body = self._compare_editor.value or ""
            self._spell_suggest_cached_src_sha = want
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()

    async def _sync_spell_candidate_for_review_tab_async(self) -> None:
        """Spell-sync for Review tab entry: heavy suggest runs off the UI thread when cache misses."""
        want = self._editor_sha256()
        if self._spell_suggest_cached_src_sha == want and self._spell_suggest_cached_body != "":
            body = self._spell_suggest_cached_body
        else:
            body = await asyncio.to_thread(
                suggest_spell_corrected_text, self.editor.value or ""
            )
            self._spell_suggest_cached_body = body
            self._spell_suggest_cached_src_sha = want
        self._compare_editor.value = body
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()

    def _kick_spell_cache_from_compose_if_needed(self) -> None:
        """Refresh background spell cache while editing on Present (Review uses it on tab enter)."""
        if self._main_tab_index != TAB_PRESENT:
            return
        self._kick_debounced_spell_suggest_cache()
