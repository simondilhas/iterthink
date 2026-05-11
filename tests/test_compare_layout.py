"""Unit tests for iterthink.compare.layout (paired compare rows)."""

from __future__ import annotations

from iterthink.compare.layout import (
    aligned_compare_pairs,
    aligned_review_rows,
    pair_paragraphs_for_compare,
)
from iterthink.compare.paragraph_compare import build_history_display_rows


def test_pair_paragraphs_for_compare_index_stable_extra_candidate_slots_empty_left() -> None:
    pairs = pair_paragraphs_for_compare("One", "One\n\nTwo\n\nThree")
    assert pairs == [("One", "One"), ("", "Two"), ("", "Three")]


def test_pair_paragraphs_for_compare_empty_candidate_yields_placeholder_pair() -> None:
    """``split_paragraphs('')`` is ``['']``, so one row compares baseline to an empty right slot."""
    assert pair_paragraphs_for_compare("x", "") == [("x", "")]


def test_aligned_compare_pairs_length_matches_candidate_paragraph_count() -> None:
    baseline = "Old first\n\nOld second"
    candidate = "New only"
    pairs = aligned_compare_pairs(baseline, candidate)
    assert len(pairs) == 1
    assert pairs[0][1] == "New only"
    assert isinstance(pairs[0][0], str)


def test_aligned_review_rows_includes_delete_kind_with_none_cand_idx() -> None:
    current = "Keep\n\nRemove me"
    ai = "Keep\n\nInserted\n\nTail"
    rows = aligned_review_rows(current, ai)
    kinds = [r.kind for r in rows]
    assert "delete" in kinds
    delete_rows = [r for r in rows if r.kind == "delete"]
    assert all(r.cand_idx is None for r in delete_rows)
    remove_row = next(r for r in delete_rows if r.old_text == "Remove me")
    assert remove_row.old_index == 1  # compose paragraph index, not UI row index
    assert remove_row.insert_after_old == -1


def test_aligned_review_rows_orders_by_candidate_with_deletes_before_target_slot() -> None:
    """Every output row after deletes still ties to candidate indices where applicable."""
    rows = aligned_review_rows("A\n\nB", "A\n\nX\n\nB")
    cand_indices = [r.cand_idx for r in rows if r.kind != "delete"]
    assert all(i is not None for i in cand_indices)


def test_aligned_review_rows_old_index_tracks_compose_slot_past_gap_rows() -> None:
    """After delete/insert gap rows, ``old_index`` must stay the compose slot (not the UI row)."""
    rows = aligned_review_rows("A\n\nB\n\nC", "A\n\nX\n\nC")
    assert rows[-1].kind == "equal"
    assert rows[-1].old_index == 2
    assert len(rows) == 4  # row index 3 would wrongly map to compose paragraph 3


def test_aligned_review_rows_insert_carries_insert_after_old() -> None:
    rows = aligned_review_rows("A\n\nB\n\nC", "A\n\nX\n\nC")
    ins = next(r for r in rows if r.kind == "insert" and r.new_text == "X")
    assert ins.old_index == -1
    assert ins.insert_after_old == 0


def test_build_history_display_rows_reorder_includes_ghosts_and_three_comparisons() -> None:
    """Review tab uses the same row model as History for move ghosts + comparison rows."""
    rows = build_history_display_rows("A\n\nB\n\nC", "C\n\nA\n\nB")
    assert sum(1 for r in rows if r.row_type == "ghost_moved") >= 1
    assert sum(1 for r in rows if r.row_type == "comparison") == 3
