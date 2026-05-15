"""Compare tab: candidates, paragraph rows, bulk AI accept.

MRO (first wins if methods ever overlap; keep this order when adding mixins):
  buffers → dispatch → diff_spans → paragraph_ui → dropdowns → bulk_actions
  → spell_review → debounce → margin_ai
"""

from __future__ import annotations

from .buffers import (
    CompareBuffers,
    _HistoryBuffersMixin,
    build_history_snapshot_dropdown_options,
    history_compare_snapshots,
    review_action_apply_label,
    snapshots_strictly_older_than,
)
from .bulk_actions import _HistoryBulkActionsMixin
from .candidate_state import CompareCandidateSource
from .debounce import _HistoryDebounceMixin
from .diff_spans import _HistoryDiffSpansMixin
from .dispatch import _HistoryDispatchMixin
from .dropdowns import _HistoryDropdownsMixin
from .margin_ai import _HistoryMarginAiMixin
from .paragraph_ui import _HistoryParagraphUIMixin
from .spell_review import _HistorySpellReviewMixin


class MarkdownStudioCompareText(
    _HistoryBuffersMixin,
    _HistoryDispatchMixin,
    _HistoryDiffSpansMixin,
    _HistoryParagraphUIMixin,
    _HistoryDropdownsMixin,
    _HistoryBulkActionsMixin,
    _HistorySpellReviewMixin,
    _HistoryDebounceMixin,
    _HistoryMarginAiMixin,
):
    """Compare tab: candidates, paragraph rows, bulk AI accept."""

    pass


__all__ = [
    "CompareBuffers",
    "CompareCandidateSource",
    "MarkdownStudioCompareText",
    "build_history_snapshot_dropdown_options",
    "history_compare_snapshots",
    "review_action_apply_label",
    "snapshots_strictly_older_than",
]
