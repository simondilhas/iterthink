"""Tests for KI comment pick-mode affordance on Review markdown rows."""

from __future__ import annotations

from dataclasses import dataclass, field

import flet as ft

from iterthink.studio.constants import TAB_FUTURE
from iterthink.studio.markdown_studio import MarkdownStudio


@dataclass
class _PickAffordanceStub:
    """Minimal host for ``_sync_ki_comment_pick_affordance``."""

    _main_tab_index: int = TAB_FUTURE
    _ki_comment_pick_mode: bool = False
    use_plan_labels: bool = False
    _future_left_diff_texts: list[ft.Text] = field(default_factory=list)
    _compare_right_fields: list[ft.TextField] = field(default_factory=list)
    _future_comment_pick_cells: list[ft.Container] = field(default_factory=list)
    _ki_comment_pick_saved_selectable: dict[int, bool] = field(default_factory=dict)
    _ki_comment_pick_saved_read_only: dict[int, bool] = field(default_factory=dict)

    def _ki_comments_use_plan_labels(self) -> bool:
        return self.use_plan_labels


def test_sync_ki_comment_pick_affordance_disables_selectable_and_read_only() -> None:
    stub = _PickAffordanceStub()
    left_text = ft.Text("para", selectable=True)
    right_field = ft.TextField(value="para", read_only=False)
    pick_cell = ft.Container()
    stub._future_left_diff_texts = [left_text]
    stub._compare_right_fields = [right_field]
    stub._future_comment_pick_cells = [pick_cell]
    stub._ki_comment_pick_mode = True

    MarkdownStudio._sync_ki_comment_pick_affordance(stub)  # type: ignore[arg-type]

    assert left_text.selectable is False
    assert right_field.read_only is True
    assert pick_cell.mouse_cursor == ft.MouseCursor.CLICK


def test_sync_ki_comment_pick_affordance_restores_prior_state() -> None:
    stub = _PickAffordanceStub()
    left_text = ft.Text("para", selectable=True)
    right_field = ft.TextField(value="para", read_only=False)
    stub._future_left_diff_texts = [left_text]
    stub._compare_right_fields = [right_field]
    stub._ki_comment_pick_mode = True

    MarkdownStudio._sync_ki_comment_pick_affordance(stub)  # type: ignore[arg-type]
    stub._ki_comment_pick_mode = False
    MarkdownStudio._sync_ki_comment_pick_affordance(stub)  # type: ignore[arg-type]

    assert left_text.selectable is True
    assert right_field.read_only is False
