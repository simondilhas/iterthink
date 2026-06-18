"""Spell review disabled — retained as no-ops for stable call sites."""


class _HistorySpellReviewMixin:
    """Spell review is disabled; methods are retained as no-ops for stable call sites."""

    def _enter_spell_review_mode(self) -> None:
        return

    def _spell_review_snack_if_no_suggestions(self) -> None:
        return

    async def _debounced_spell_suggest_cache(self, gen: int) -> None:
        return

    def _kick_debounced_spell_suggest_cache(self) -> None:
        return

    def _clear_spell_suggest_cache(self) -> None:
        self._spell_suggest_gen = int(getattr(self, "_spell_suggest_gen", 0)) + 1
        self._spell_suggest_cached_body = ""
        self._spell_suggest_cached_src_sha = None

    def _sync_spell_candidate_from_cache(self) -> None:
        return

    async def _sync_spell_candidate_for_review_tab_async(self) -> None:
        return

    def _kick_spell_cache_from_compose_if_needed(self) -> None:
        return
