"""Tests for deferred compare rebuild when compare tabs are hidden."""

from iterthink.studio.constants import TAB_FUTURE, TAB_HISTORY, TAB_PRESENT
from iterthink.studio.history.debounce import _HistoryDebounceMixin


class _Stub(_HistoryDebounceMixin):
    def __init__(self, tab: int) -> None:
        self._main_tab_index = tab
        self._compare_rebuild_pending = False
        self.rebuilt = False

    def _compare_tab_is_active(self) -> bool:
        return self._main_tab_index in (TAB_HISTORY, TAB_FUTURE)

    def _mark_compare_rebuild_pending(self) -> None:
        self._compare_rebuild_pending = True

    def _clear_compare_rebuild_pending(self) -> None:
        self._compare_rebuild_pending = False

    def _rebuild_future_paragraph_ui(self) -> None:
        self.rebuilt = True

    def _rebuild_compare_view(self) -> None:
        self.rebuilt = True


def test_refresh_compare_diff_deferred_on_present() -> None:
    stub = _Stub(TAB_PRESENT)
    stub._refresh_compare_diff_immediate()
    assert stub._compare_rebuild_pending is True
    assert stub.rebuilt is False


def test_refresh_compare_diff_runs_on_history() -> None:
    stub = _Stub(TAB_HISTORY)
    stub._refresh_compare_diff_immediate()
    assert stub._compare_rebuild_pending is False
    assert stub.rebuilt is True
