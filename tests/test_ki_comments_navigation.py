"""Tests for KI Comments navigation, grouping, and scroll index mapping."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import flet as ft

from iterthink import checks as checks_mod
from iterthink.checks import Check, CheckSymbol
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, paragraph_user_comments
from iterthink.studio.checks_ui import MarkdownStudioChecksUi
from iterthink.studio.constants import TAB_FUTURE, TAB_HISTORY
from iterthink.studio.history.compare_virtual import (
    compare_row_scroll_key,
    display_index_for_cand_idx,
)
from iterthink.studio.ki_comments import (
    CommentThreadItem,
    analyse_list_key,
    paragraph_comment_label,
)
from iterthink.studio.ki_comments_sidebar import KiCommentsSidebarMixin


def _comparison_row(new_paragraph_index: int) -> SimpleNamespace:
    return SimpleNamespace(
        row_type="comparison",
        new_paragraph_index=new_paragraph_index,
    )


def _removed_row() -> SimpleNamespace:
    return SimpleNamespace(row_type="removed", new_paragraph_index=-1)


def test_display_index_for_cand_idx_maps_through_comp_idx() -> None:
    """Removed rows shift comp_idx; cand_idx at doc end must not use raw index."""
    display_rows = [
        _removed_row(),
        _comparison_row(0),
        _comparison_row(1),
        _comparison_row(2),
    ]
    eval_cand_indices = [0, 1, 2]
    comp_display_index = {0: 1, 1: 2, 2: 3}
    assert display_index_for_cand_idx(
        2,
        eval_cand_indices=eval_cand_indices,
        comp_display_index=comp_display_index,
        display_rows=display_rows,
    ) == 3


def test_display_index_for_cand_idx_fallback_scans_display_rows() -> None:
    rows = [_comparison_row(5), _comparison_row(9)]
    assert display_index_for_cand_idx(
        9,
        eval_cand_indices=None,
        comp_display_index={},
        display_rows=rows,
    ) == 1


class _GroupedCommentsStub(KiCommentsSidebarMixin):
    def __init__(self, items: list[CommentThreadItem]) -> None:
        self._items = items

    def _ki_comments_for_current_version(self) -> dict[int, str]:
        return {}

    def _ki_comment_thread_items(self) -> list[CommentThreadItem]:
        return list(self._items)


def test_grouped_analysis_items_one_header_per_check() -> None:
    check = Check(
        id="readability",
        label="Readability",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )
    items = [
        CommentThreadItem(
            kind="analyse",
            paragraph_index=0,
            title=paragraph_comment_label(0),
            preview="Issue A",
            list_key=analyse_list_key(0, "readability"),
            check_id="readability",
            symbol="!",
        ),
        CommentThreadItem(
            kind="analyse",
            paragraph_index=2,
            title=paragraph_comment_label(2),
            preview="Issue C",
            list_key=analyse_list_key(2, "readability"),
            check_id="readability",
            symbol="!",
        ),
    ]
    stub = _GroupedCommentsStub(items)
    groups = stub._ki_grouped_analysis_items(items)
    assert len(groups) == 1
    assert groups[0][0] == "analyse:readability"
    assert groups[0][1] == "Readability"
    assert len(groups[0][2]) == 2
    assert all(it.title == paragraph_comment_label(it.paragraph_index) for it in items)
    assert checks_mod.get_check("readability") is not None or check.label == "Readability"


class _RestoreCheckStub(MarkdownStudioChecksUi):
    def __init__(self, *, vid: int, pairs: list[tuple[str, str]], check_id: str) -> None:
        self.current_path = Path("/tmp/note.md")
        self._main_tab_index = TAB_FUTURE
        self._active_check_id = None
        self._check_results: dict[str, list] = {}
        self._check_run_gen: dict[str, int] = {}
        self._check_running: dict[str, bool] = {}
        self._check_para_hashes: list[str] = []
        self._compare_eval_hosts = []
        self._analyse_buttons = {}
        self._analyse_button_progress = {}
        self._analyse_button_count = {}
        self._analyse_compare_version_pairs: dict[str, tuple[int | None, int | None]] = {}
        self._review_baseline_version_id: int | None = None
        self._compare_snapshot_version_id: int | None = None
        self._vid = vid
        self._pairs = pairs
        self._seed_check_id = check_id

    def _review_text_single_layout_active(self) -> bool:
        return False

    def _active_compare_buffers(self):
        baseline = "\n\n".join(p[0] for p in self._pairs)
        candidate = "\n\n".join(p[1] for p in self._pairs)
        return SimpleNamespace(baseline=baseline, candidate=candidate)

    def _resolve_impact_version_id(self, session) -> int | None:  # noqa: ANN001
        return int(self._vid)

    def _refresh_analyse_button_state(self) -> None:
        pass

    def _refresh_all_eval_cells(self) -> None:
        pass


def test_restore_active_check_from_db_sets_active_check(
    ephemeral_store: None, tmp_path: Path, monkeypatch
) -> None:
    body = "Alpha\n\nBeta"
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=int(vid),
            paragraph_index=1,
            check_id="unit_restore",
            body="Beta note",
            symbol="!",
            payload={"impact_symbol": "!", "summary": "Beta note"},
            candidate_paragraph="Beta",
        )

    check = Check(
        id="unit_restore",
        label="Restore test",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )
    monkeypatch.setattr(checks_mod, "CHECKS", (check,))
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)

    pairs = [("Alpha", "Alpha"), ("Beta", "Beta")]
    stub = _RestoreCheckStub(vid=int(vid), pairs=pairs, check_id=check.id)
    stub._restore_active_check_from_db()
    assert stub._active_check_id == check.id
    results = stub._check_results.get(check.id) or []
    assert len(results) == 2
    assert isinstance(results[1], dict)
    assert results[1].get("summary") == "Beta note"


def test_dismiss_analyse_review_display_clears_active_check(
    ephemeral_store: None, tmp_path: Path, monkeypatch
) -> None:
    body = "Alpha\n\nBeta"
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None

    check = Check(
        id="unit_dismiss",
        label="Dismiss test",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )
    monkeypatch.setattr(checks_mod, "CHECKS", (check,))

    pairs = [("Alpha", "Alpha"), ("Beta", "Beta")]
    stub = _RestoreCheckStub(vid=int(vid), pairs=pairs, check_id=check.id)
    stub._activate_analyse_check_for_display(check.id)
    assert stub._active_check_id == check.id
    stub._ki_comment_focus = ("analyse", 1, check.id)
    stub._dismiss_analyse_review_display()
    assert stub._active_check_id is None
    assert stub._ki_comment_focus is None


def test_activate_analyse_check_for_display_hydrates_from_db(
    ephemeral_store: None, tmp_path: Path, monkeypatch
) -> None:
    body = "One\n\nTwo"
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=int(vid),
            paragraph_index=0,
            check_id="unit_activate",
            body="First issue",
            symbol="!",
            payload={"impact_symbol": "!", "summary": "First issue"},
            candidate_paragraph="One",
        )

    check = Check(
        id="unit_activate",
        label="Activate test",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)

    pairs = [("One", "One"), ("Two", "Two")]
    stub = _RestoreCheckStub(vid=int(vid), pairs=pairs, check_id=check.id)
    assert stub._active_check_id is None
    stub._activate_analyse_check_for_display(check.id)
    assert stub._active_check_id == check.id
    results = stub._check_results.get(check.id) or []
    assert isinstance(results[0], dict)
    assert results[0].get("summary") == "First issue"
    assert results[1] is None


@pytest.mark.asyncio
async def test_run_check_async_skips_llm_when_db_fully_hydrated(
    ephemeral_store: None, tmp_path: Path, monkeypatch
) -> None:
    body = "A\n\nB"
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None
        for i, para in enumerate(("A", "B")):
            paragraph_user_comments.upsert_analyse(
                s,
                content_version_id=int(vid),
                paragraph_index=i,
                check_id="unit_skip_run",
                body=f"Note {i}",
                symbol="!",
                payload={"impact_symbol": "!", "summary": f"Note {i}"},
                candidate_paragraph=para,
            )

    check = Check(
        id="unit_skip_run",
        label="Skip run",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)

    async def _should_not_run(*_a, **_k):
        raise AssertionError("run_check_for_document should not be called")

    monkeypatch.setattr(
        "iterthink.studio.checks_ui.checks_runner.run_check_for_document",
        _should_not_run,
    )

    class _RunStub(_RestoreCheckStub):
        _main_tab_index = TAB_FUTURE

        def __init__(self, *, vid: int, pairs: list[tuple[str, str]], check_id: str) -> None:
            super().__init__(vid=vid, pairs=pairs, check_id=check_id)

        def _compare_comp_slot_count(self) -> int:
            return len(self._pairs)

        async def _request_tab_switch_async(self, _tab: int) -> None:
            pass

        def _rebuild_future_paragraph_ui(self) -> None:
            pass

        def _snack(self, _msg: str) -> None:
            pass

        def chat_model_for_requests(self) -> str:
            return "test-model"

        def _make_llm_backend(self):
            return None

    pairs = [("A", "A"), ("B", "B")]
    stub = _RunStub(vid=int(vid), pairs=pairs, check_id=check.id)
    await stub._run_check_async(check.id)
    assert stub._active_check_id == check.id
    assert all(isinstance(r, dict) for r in stub._check_results.get(check.id, []))


class _ScrollWorkspaceStub(KiCommentsSidebarMixin):
    def __init__(self, *, tab: int = TAB_FUTURE) -> None:
        self._main_tab_index = tab
        self._scroll_calls: list[dict[str, object]] = []
        self._highlighted: int | None = None
        lv = MagicMock()
        lv.page = MagicMock()
        lv.visible = True

        async def _scroll_to(**kwargs: object) -> None:
            self._scroll_calls.append(dict(kwargs))

        lv.scroll_to = _scroll_to
        if tab == TAB_FUTURE:
            self._future_rows_listview = lv
        else:
            self._compare_rows_listview = lv

    def _ui_idx_for_eval_cand_idx(self, cand_idx: int) -> int:
        return int(cand_idx)

    def _highlight_compare_row_ui_idx(self, ui_idx: int | None) -> None:
        self._highlighted = ui_idx


@pytest.mark.asyncio
async def test_scroll_workspace_prefers_scroll_key_on_review() -> None:
    stub = _ScrollWorkspaceStub(tab=TAB_FUTURE)
    await stub._scroll_workspace_to_paragraph_async(2)
    assert stub._scroll_calls == [{"scroll_key": compare_row_scroll_key(2), "duration": 150}]
    assert stub._highlighted == 2


@pytest.mark.asyncio
async def test_scroll_workspace_prefers_scroll_key_on_history() -> None:
    stub = _ScrollWorkspaceStub(tab=TAB_HISTORY)
    await stub._scroll_workspace_to_paragraph_async(5)
    assert stub._scroll_calls == [{"scroll_key": compare_row_scroll_key(5), "duration": 150}]
    assert stub._highlighted == 5


@pytest.mark.asyncio
async def test_scroll_workspace_does_not_offset_fallback_when_key_fails() -> None:
    stub = _ScrollWorkspaceStub(tab=TAB_FUTURE)
    calls: list[dict[str, object]] = []

    async def _scroll_to(**kwargs: object) -> None:
        calls.append(dict(kwargs))
        if "scroll_key" in kwargs:
            raise TypeError("scroll_key unsupported")

    stub._future_rows_listview.scroll_to = _scroll_to
    await stub._scroll_workspace_to_paragraph_async(2)
    assert calls == [{"scroll_key": compare_row_scroll_key(2), "duration": 150}]
    assert stub._highlighted is None


def _text_values_in_ctrl(ctrl: ft.Control) -> list[str]:
    vals: list[str] = []
    if isinstance(ctrl, ft.Text) and ctrl.value:
        vals.append(str(ctrl.value))
    content = getattr(ctrl, "content", None)
    if content is not None:
        if isinstance(content, list):
            for child in content:
                vals.extend(_text_values_in_ctrl(child))
        else:
            vals.extend(_text_values_in_ctrl(content))
    controls = getattr(ctrl, "controls", None)
    if controls:
        for child in controls:
            vals.extend(_text_values_in_ctrl(child))
    return vals


class _EvalSymbolStub(MarkdownStudioChecksUi):
    def __init__(
        self,
        *,
        check: Check,
        single_layout: bool = False,
        baseline_vid: int | None = 1,
        candidate_vid: int | None = 3,
        stored_pair: tuple[int | None, int | None] | None = None,
        payload: dict | None = None,
    ) -> None:
        self.current_path = Path("/tmp/note.md")
        self._main_tab_index = TAB_FUTURE
        self._single_layout = single_layout
        self._review_baseline_version_id = baseline_vid
        self._compare_snapshot_version_id = candidate_vid
        self._active_check_id = check.id
        self._check = check
        self._check_results = {check.id: [payload, None]}
        self._check_running: dict[str, bool] = {}
        self._check_run_gen: dict[str, int] = {}
        self._check_para_hashes: list[str] = []
        self._compare_eval_hosts = []
        self._analyse_compare_version_pairs: dict[str, tuple[int | None, int | None]] = {}
        if stored_pair is not None:
            self._analyse_compare_version_pairs[check.id] = stored_pair

    def _review_text_single_layout_active(self) -> bool:
        return self._single_layout

    def _active_compare_buffers(self):
        return SimpleNamespace(baseline="Old", candidate="New")

    def _eval_cand_idx(self, ui_idx: int) -> int | None:
        return ui_idx

    def _resolve_impact_version_id(self, session) -> int | None:  # noqa: ANN001
        return 3

    def _analyse_payload_for_cand_idx(self, check_id: str, cand_idx: int):
        results = self._check_results.get(check_id) or []
        if 0 <= cand_idx < len(results):
            row = results[cand_idx]
            return row if isinstance(row, dict) else None
        return None


def _unit_eval_check() -> Check:
    return Check(
        id="unit_eval_gate",
        label="Eval gate",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )


def test_eval_symbols_hidden_in_single_review_layout(monkeypatch) -> None:
    check = _unit_eval_check()
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)
    stub = _EvalSymbolStub(
        check=check,
        single_layout=True,
        stored_pair=(1, 3),
        payload={"impact_symbol": "!", "summary": "Issue"},
    )
    inner = stub._build_eval_cell_inner(0, check.id)
    assert "!" not in _text_values_in_ctrl(inner)


def test_eval_symbols_hidden_when_version_pair_mismatch(monkeypatch) -> None:
    check = _unit_eval_check()
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)
    stub = _EvalSymbolStub(
        check=check,
        baseline_vid=2,
        candidate_vid=3,
        stored_pair=(1, 3),
        payload={"impact_symbol": "!", "summary": "Issue"},
    )
    inner = stub._build_eval_cell_inner(0, check.id)
    assert "!" not in _text_values_in_ctrl(inner)


def test_eval_symbols_visible_when_version_pair_matches(monkeypatch) -> None:
    check = _unit_eval_check()
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)
    stub = _EvalSymbolStub(
        check=check,
        baseline_vid=1,
        candidate_vid=3,
        stored_pair=(1, 3),
        payload={"impact_symbol": "!", "summary": "Issue"},
    )
    inner = stub._build_eval_cell_inner(0, check.id)
    assert "!" in _text_values_in_ctrl(inner)


def test_legacy_analyse_without_pair_metadata_hides_symbols(
    ephemeral_store: None, tmp_path: Path, monkeypatch
) -> None:
    body = "Alpha\n\nBeta"
    md = tmp_path / "note.md"
    md.write_text(body, encoding="utf-8")
    with session_scope() as s:
        vid = content_repo.persist_version_snapshot(s, md.resolve(), body, "manual")
        assert vid is not None
        paragraph_user_comments.upsert_analyse(
            s,
            content_version_id=int(vid),
            paragraph_index=1,
            check_id="unit_legacy",
            body="Beta note",
            symbol="!",
            payload={"impact_symbol": "!", "summary": "Beta note"},
            candidate_paragraph="Beta",
        )

    check = Check(
        id="unit_legacy",
        label="Legacy",
        accent="#5AB0FF",
        system_prompt="JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="!", label="Issue", color="#FF0000"),),
    )
    monkeypatch.setattr(checks_mod, "get_check", lambda cid: check if cid == check.id else None)

    pairs = [("Alpha", "Alpha"), ("Beta", "Beta")]
    stub = _RestoreCheckStub(vid=int(vid), pairs=pairs, check_id=check.id)
    stub._review_baseline_version_id = 1
    stub._compare_snapshot_version_id = int(vid)
    stub._activate_analyse_check_for_display(check.id)
    assert stub._active_check_id == check.id
    inner = stub._build_eval_cell_inner(1, check.id)
    assert "!" not in _text_values_in_ctrl(inner)
