"""Tests for main workspace tab switch queue and UI reconciliation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from iterthink.studio.constants import TAB_FUTURE, TAB_HISTORY, TAB_PRESENT
from iterthink.studio.main_workspace_tabs import MainWorkspaceTabsMixin


class _PostOpenRefreshStub(MainWorkspaceTabsMixin):
    def __init__(self, tab_index: int) -> None:
        self._init_main_workspace_tab_fields()
        self._main_tab_index = tab_index
        self.current_path = Path("/tmp/note.md")
        self.present_calls = 0
        self.history_calls = 0
        self.review_calls = 0
        self.chrome_calls = 0
        self.diff_calls = 0

    async def _load_present_tab_content_async(self, switch_seq: int | None = None) -> bool:
        self.present_calls += 1
        return True

    async def _load_history_tab_content_async(
        self,
        *,
        reset_newer_side: bool = False,
        pick_autosave: bool = False,
    ) -> None:
        self.history_calls += 1
        assert reset_newer_side is True
        assert pick_autosave is True

    async def _load_review_tab_content_async(
        self,
        *,
        show_spinner: bool = True,
        switch_seq: int | None = None,
    ) -> bool:
        self.review_calls += 1
        assert show_spinner is False
        return True

    def _refresh_compare_tab_candidate_ui(self) -> None:
        self.chrome_calls += 1

    def _apply_compare_candidate_dropdown_tab_chrome(self) -> None:
        pass

    def _apply_active_tab_ui_state(self) -> None:
        pass

    def _refresh_compare_diff_immediate(self) -> None:
        self.diff_calls += 1


class _TabSwitchUiStub(MainWorkspaceTabsMixin):
    def __init__(self) -> None:
        self._init_main_workspace_tab_fields()
        self._main_tab_index = TAB_PRESENT
        self.current_path = Path("/tmp/note.md")
        self._main_tabs = MagicMock()
        self._main_tabs.selected_index = TAB_HISTORY
        self._review_change_panel = MagicMock(visible=False, expand=False)
        self._review_impact_panel = MagicMock(visible=False, expand=False)
        self._review_subtab_index = 0
        self.save_calls = 0
        self._stale_after_save = False

    def _is_dirty(self) -> bool:
        return True

    async def save_file(self, *args, **kwargs) -> None:
        self.save_calls += 1
        if self._stale_after_save:
            self._tab_switch_seq += 1

    def _flush_review_edits_if_changed(self, **kwargs) -> None:
        pass

    def _cancel_autosave_timers(self) -> None:
        pass

    def _refresh_tab_toolbar(self) -> None:
        pass

    def _apply_focus_preview_mode(self) -> None:
        pass

    def _hide_all_result_card_overlays(self) -> None:
        pass

    def _refresh_compare_tab_candidate_ui(self) -> None:
        pass

    def _apply_compare_candidate_dropdown_tab_chrome(self) -> None:
        pass

    def _refresh_title_bar(self) -> None:
        pass


def test_stale_tab_switch_after_pre_switch_save_restores_tab_bar() -> None:
    stub = _TabSwitchUiStub()
    stub._stale_after_save = True
    stub._tab_switch_seq = 1
    assert stub._main_tabs.selected_index == TAB_HISTORY

    asyncio.run(stub._sync_tab_switch_async(TAB_HISTORY, switch_seq=1))

    assert stub.save_calls == 1
    assert stub._main_tab_index == TAB_PRESENT
    assert stub._main_tabs.selected_index == TAB_PRESENT


def test_tab_switch_timeout_restores_previous_tab() -> None:
    stub = _TabSwitchUiStub()

    async def _hang(*_a: object, **_k: object) -> None:
        await asyncio.sleep(60)

    with patch.object(stub, "_sync_tab_switch_async", side_effect=_hang):
        with patch(
            "iterthink.studio.main_workspace_tabs.TAB_SWITCH_TIMEOUT_SEC",
            0.05,
        ):
            asyncio.run(stub._sync_tab_switch_with_timeout(TAB_HISTORY, switch_seq=1))

    assert stub._main_tab_index == TAB_PRESENT
    assert stub._main_tabs.selected_index == TAB_PRESENT


def test_kick_debounced_autosave_schedules_single_coroutine() -> None:
    from iterthink.studio.focus_area import MarkdownStudioCompose

    class _AutosaveStub(MarkdownStudioCompose):
        def __init__(self) -> None:
            self.current_path = Path("/tmp/note.md")
            self._disk_autosave_gen = 0
            self._snapshot_autosave_gen = 0
            self._autosave_scheduler_running = False
            self._run_tasks: list[tuple] = []
            self.page = MagicMock(
                run_task=lambda fn, *args: self._run_tasks.append((fn, args))
            )

        def _is_dirty(self) -> bool:
            return True

    stub = _AutosaveStub()
    stub._kick_debounced_autosave()
    stub._kick_debounced_autosave()
    assert stub._disk_autosave_gen == 2
    assert len(stub._run_tasks) == 1
    assert stub._run_tasks[0][0].__name__ == "_autosave_scheduler_async"


@pytest.mark.parametrize(
    ("tab", "present", "history", "review"),
    [
        (TAB_PRESENT, 1, 0, 0),
        (TAB_HISTORY, 0, 1, 0),
        (TAB_FUTURE, 0, 0, 1),
    ],
)
def test_refresh_active_tab_after_document_open_dispatches_by_tab(
    tab: int,
    present: int,
    history: int,
    review: int,
) -> None:
    stub = _PostOpenRefreshStub(tab)
    asyncio.run(stub._refresh_active_tab_after_document_open_async())
    assert stub._main_tab_index == tab
    assert stub.present_calls == present
    assert stub.history_calls == history
    assert stub.review_calls == review
    assert stub.chrome_calls == 1
    assert stub.diff_calls == 1
