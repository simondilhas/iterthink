"""Tests for Review-tab markdown text single/compare layout modes."""

from __future__ import annotations

from dataclasses import dataclass, field

import flet as ft
import pytest

from iterthink.studio.constants import TAB_FUTURE, TAB_PRESENT
from iterthink.studio.formats.pdf_docx import (
    MarkdownStudioAssetCompare,
    _TEXT_LAYOUT_ORDER,
)
from iterthink.studio.history.candidate_state import CompareCandidateSource
from iterthink.studio.history.paragraph_ui import _HistoryParagraphUIMixin


@dataclass
class _FakePlanPanel:
    overlay_list: ft.ListView = field(default_factory=ft.ListView)
    baseline_dd: ft.Dropdown = field(default_factory=ft.Dropdown)
    candidate_dd: ft.Dropdown = field(default_factory=ft.Dropdown)
    baseline_wrap: ft.Container = field(default_factory=ft.Container)
    candidate_wrap: ft.Container = field(default_factory=ft.Container)
    baseline_label: ft.Text = field(default_factory=lambda: ft.Text("Baseline:"))
    host: ft.Container = field(default_factory=ft.Container)

    def set_bar_visible(self, visible: bool) -> None:
        self.host.visible = visible


class _TextLayoutStub(MarkdownStudioAssetCompare):
    def __init__(self) -> None:
        self.current_path = None
        self._main_tab_index = TAB_FUTURE
        self._review_subtab_index = 0
        self._plan_layout_mode = "side_by_side"
        self._compare_candidate_source = CompareCandidateSource.AI_PREVIEW
        self._plan_layout_menu_btn = ft.PopupMenuButton(items=[])
        self._plan_compare_future = _FakePlanPanel()
        self._review_baseline_chrome_col = ft.Container(visible=True)
        self._review_baseline_dropdown = ft.Dropdown(visible=True)
        self.future_paragraph_rebuilds = 0

    def _plan_pdf_version_count(self) -> int:
        return 0

    def _is_plan_pdf_compare(self) -> bool:
        return False

    def _rebuild_future_paragraph_ui(self) -> None:
        self.future_paragraph_rebuilds += 1


class _ParagraphRowStub(_HistoryParagraphUIMixin):
    def __init__(self, *, text_single: bool = False) -> None:
        self._review_text_single_mode = lambda: text_single  # type: ignore[method-assign]


@pytest.mark.parametrize(
    ("tab", "subtab", "source", "mode", "expected"),
    [
        (TAB_FUTURE, 0, CompareCandidateSource.AI_PREVIEW, "single", True),
        (TAB_FUTURE, 0, CompareCandidateSource.AI_PREVIEW, "side_by_side", False),
        (TAB_FUTURE, 1, CompareCandidateSource.AI_PREVIEW, "single", False),
        (TAB_PRESENT, 0, CompareCandidateSource.AI_PREVIEW, "single", False),
        (TAB_FUTURE, 0, CompareCandidateSource.PDF_ORIGINAL, "single", False),
        (TAB_FUTURE, 0, CompareCandidateSource.AI_PREVIEW, "overlay", False),
    ],
)
def test_review_text_single_mode(
    tab: int, subtab: int, source: CompareCandidateSource, mode: str, expected: bool
) -> None:
    stub = _TextLayoutStub()
    stub._main_tab_index = tab
    stub._review_subtab_index = subtab
    stub._compare_candidate_source = source
    stub._plan_layout_mode = mode
    assert stub._review_text_single_mode() is expected


def test_plan_layout_chrome_active_for_text_review() -> None:
    stub = _TextLayoutStub()
    assert stub._plan_layout_chrome_active() is True
    stub._compare_candidate_source = CompareCandidateSource.PDF_ORIGINAL
    assert stub._plan_layout_chrome_active() is False


def test_text_layout_menu_items_exclude_overlay() -> None:
    stub = _TextLayoutStub()
    items = stub._build_plan_layout_menu_items("side_by_side", multi=True)
    labels = [item.content.controls[-1].value for item in items]  # type: ignore[attr-defined]
    assert labels == ["Single document", "Compare old and new"]
    assert _TEXT_LAYOUT_ORDER == ("single", "side_by_side")


def test_sync_plan_overlay_pane_visibility_does_not_force_single_for_text_review() -> None:
    stub = _TextLayoutStub()
    stub._plan_layout_mode = "side_by_side"
    stub._sync_plan_overlay_pane_visibility()
    assert stub._plan_layout_mode == "side_by_side"


def test_sync_plan_overlay_pane_visibility_still_forces_single_for_plan_without_versions() -> None:
    stub = _TextLayoutStub()
    stub._compare_candidate_source = CompareCandidateSource.PDF_ORIGINAL
    stub._plan_layout_mode = "overlay"

    def _is_plan_pdf_compare() -> bool:
        return True

    stub._is_plan_pdf_compare = _is_plan_pdf_compare  # type: ignore[method-assign]
    stub._sync_plan_overlay_pane_visibility()
    assert stub._plan_layout_mode == "single"


def test_ensure_text_review_compare_layout_default() -> None:
    stub = _TextLayoutStub()
    stub._plan_layout_mode = "single"
    stub._ensure_text_review_compare_layout_default()
    assert stub._plan_layout_mode == "side_by_side"

    stub._text_review_user_layout_mode = "single"
    stub._plan_layout_mode = "side_by_side"
    stub._ensure_text_review_compare_layout_default()
    assert stub._plan_layout_mode == "single"


def test_set_plan_layout_mode_user_chosen_remembers_single() -> None:
    stub = _TextLayoutStub()
    stub._set_plan_layout_mode("single", user_chosen=True, rebuild=False)
    assert stub._text_review_user_layout_mode == "single"
    stub._plan_layout_mode = "side_by_side"
    stub._ensure_text_review_compare_layout_default()
    assert stub._plan_layout_mode == "single"


def test_set_plan_layout_mode_rebuilds_text_review() -> None:
    stub = _TextLayoutStub()
    stub._set_plan_layout_mode("single")
    assert stub._plan_layout_mode == "single"
    assert stub.future_paragraph_rebuilds == 1
    stub._set_plan_layout_mode("overlay", rebuild=True)
    assert stub._plan_layout_mode == "side_by_side"
    assert stub.future_paragraph_rebuilds == 2


def test_sync_review_text_layout_chrome_hides_current_column() -> None:
    stub = _TextLayoutStub()
    stub._plan_layout_mode = "single"
    stub._sync_review_text_layout_chrome()
    assert stub._review_baseline_chrome_col.visible is False
    stub._plan_layout_mode = "side_by_side"
    stub._sync_review_text_layout_chrome()
    assert stub._review_baseline_chrome_col.visible is True


def test_future_review_visible_row_cells_single_vs_compare() -> None:
    eval_ctrl = ft.Container(width=36)
    left = ft.Container(expand=1)
    pill = ft.Container(width=20)
    right = ft.Container(expand=1)
    stub = _ParagraphRowStub(text_single=False)
    compare_cells = stub._future_review_visible_row_cells(
        text_single=False,
        eval_ctrl=eval_ctrl,
        left_cell=left,
        pill_host=pill,
        right_cell=right,
    )
    assert compare_cells == [eval_ctrl, left, pill, right]
    single_cells = stub._future_review_visible_row_cells(
        text_single=True,
        eval_ctrl=eval_ctrl,
        left_cell=left,
        pill_host=pill,
        right_cell=right,
    )
    assert single_cells == [right]


def test_sync_plan_compare_baseline_chrome_hides_in_plan_single_mode() -> None:
    stub = _TextLayoutStub()
    stub._compare_candidate_source = CompareCandidateSource.PDF_ORIGINAL
    stub._plan_layout_mode = "single"
    stub._version_count = 2  # type: ignore[attr-defined]

    def _plan_pdf_version_count() -> int:
        return 2

    def _is_plan_pdf_compare() -> bool:
        return True

    stub._plan_pdf_version_count = _plan_pdf_version_count  # type: ignore[method-assign]
    stub._is_plan_pdf_compare = _is_plan_pdf_compare  # type: ignore[method-assign]
    stub._sync_plan_compare_baseline_chrome()
    assert stub._plan_compare_future.baseline_label.visible is False
    assert stub._plan_compare_future.baseline_wrap.visible is False
