"""Unit tests for iterthink.compare.paragraph_compare (slot kinds and history rows)."""

from __future__ import annotations

from iterthink.compare.paragraph_compare import (
    SlotKind,
    build_history_display_rows,
    compare_slots_heuristic,
    slot_kind_label,
    slot_kinds_heuristic,
)


def test_slot_kind_label_covers_all_slot_kinds() -> None:
    kinds: list[SlotKind] = [
        "stable",
        "refined",
        "modified",
        "rephrased",
        "added",
        "removed",
    ]
    labels = {slot_kind_label(k) for k in kinds}
    assert "—" in labels
    assert "Added" in labels
    assert "Removed" in labels


def test_compare_slots_heuristic_appended_paragraph_is_added() -> None:
    kinds, disps = compare_slots_heuristic("A", "A\n\nB")
    assert kinds == ["stable", "added"]
    assert disps == [None, None]


def test_slot_kinds_heuristic_matches_compare_first_element() -> None:
    assert slot_kinds_heuristic("A", "A\n\nB") == ["stable", "added"]


def test_build_history_display_rows_identical_no_ghosts() -> None:
    rows = build_history_display_rows("One\n\nTwo", "One\n\nTwo")
    assert all(r.row_type == "comparison" for r in rows)
    assert not any(r.row_type == "ghost_moved" for r in rows)
    assert [r.slot_kind for r in rows] == ["stable", "stable"]


def test_build_history_display_rows_trailing_addition() -> None:
    rows = build_history_display_rows("A", "A\n\nB")
    types = [r.row_type for r in rows]
    assert types == ["comparison", "comparison"]
    assert rows[1].slot_kind == "added"
    assert rows[1].old_text == "" and rows[1].new_text == "B"


def test_build_history_display_rows_deletion_emits_removed_row() -> None:
    rows = build_history_display_rows("A\n\nB", "A")
    removed = [r for r in rows if r.row_type == "removed"]
    assert len(removed) == 1
    assert removed[0].old_text == "B"
    assert any(r.row_type == "comparison" and r.new_text == "A" for r in rows)


def test_build_history_display_rows_reorder_includes_true_mover_ghost() -> None:
    rows = build_history_display_rows("A\n\nB\n\nC", "C\n\nA\n\nB")
    ghosts = [r for r in rows if r.row_type == "ghost_moved"]
    assert len(ghosts) >= 1
    assert all(r.is_true_mover for r in ghosts)
    for g in ghosts:
        assert g.old_paragraph_index >= 0
        assert g.new_paragraph_index >= 0


def test_build_history_display_rows_removed_row_indices() -> None:
    rows = build_history_display_rows("A\n\nB", "A")
    rem = next(r for r in rows if r.row_type == "removed")
    assert rem.old_paragraph_index == 1
    assert rem.new_paragraph_index == -1
    comp = next(r for r in rows if r.row_type == "comparison" and r.new_text == "A")
    assert comp.old_paragraph_index == 0
    assert comp.new_paragraph_index == 0


def test_build_history_display_rows_trailing_addition_indices() -> None:
    rows = build_history_display_rows("A", "A\n\nB")
    added = rows[1]
    assert added.row_type == "comparison" and added.slot_kind == "added"
    assert added.old_paragraph_index == -1
    assert added.new_paragraph_index == 1
