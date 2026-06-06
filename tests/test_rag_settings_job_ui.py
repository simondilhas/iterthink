"""Tests for persisted RAG job UI state in Settings."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import asyncio

from iterthink.studio.search_results_ui import MarkdownStudioSearchResults


class _RagJobUiStub(MarkdownStudioSearchResults):
    def __init__(self) -> None:
        self._init_search_results_ui()


class _MockText:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.updated = False

    def update(self) -> None:
        self.updated = True


def test_set_rag_status_line_persists_without_control() -> None:
    stub = _RagJobUiStub()
    stub._set_rag_status_line("Indexing…")
    assert stub._rag_status_line_value == "Indexing…"
    assert stub._rag_settings_status_line_text is None


def test_apply_rag_job_ui_restores_status_after_settings_build() -> None:
    stub = _RagJobUiStub()
    stub._set_rag_index_progress(True, current=2, total=5, name="doc.md")

    status = _MockText("Idle")
    bar = SimpleNamespace(visible=False, value=None, updated=False)
    label = SimpleNamespace(visible=False, value="", updated=False)
    btn = SimpleNamespace(disabled=False, updated=False)
    rebuild_btn = SimpleNamespace(disabled=False, updated=False)

    def _bar_update() -> None:
        bar.updated = True

    def _label_update() -> None:
        label.updated = True

    def _btn_update() -> None:
        btn.updated = True

    def _rebuild_update() -> None:
        rebuild_btn.updated = True

    bar.update = _bar_update  # type: ignore[method-assign]
    label.update = _label_update  # type: ignore[method-assign]
    btn.update = _btn_update  # type: ignore[method-assign]
    rebuild_btn.update = _rebuild_update  # type: ignore[method-assign]

    stub._rag_settings_status_line_text = status
    stub._rag_settings_progress_bar = bar
    stub._rag_settings_progress_label = label
    stub._rag_settings_reindex_btn = btn
    stub._rag_settings_rebuild_btn = rebuild_btn

    with patch("iterthink.studio.search_results_ui._ctrl_on_page", return_value=True):
        stub._apply_rag_job_ui()

    assert status.value == "Indexing 2 / 5 — doc.md"
    assert status.updated is True
    assert bar.visible is True
    assert bar.value == 0.4
    assert label.visible is True
    assert label.value == "2 / 5 — doc.md"
    assert btn.disabled is True
    assert rebuild_btn.disabled is True


def test_hydrate_rag_status_on_startup_sets_idle_line() -> None:
    stub = _RagJobUiStub()
    status = _MockText("Idle")
    documents = _MockText("—")
    stub._rag_settings_status_line_text = status
    stub._rag_settings_documents_text = documents
    stub._rag_settings_index_size_text = _MockText("—")
    stub._rag_settings_last_indexed_text = _MockText("—")
    stub._rag_settings_active_chunks_text = _MockText("—")
    stub._rag_settings_historical_chunks_text = _MockText("—")
    stub.page = SimpleNamespace(web=False)

    fake_stats = {
        "documents": "2 / 3 indexed",
        "index_size": "1 KB",
        "last_indexed": "2026-01-01 12:00",
        "active_chunks": "10",
        "historical_chunks": "0",
    }

    with patch("iterthink.studio.search_results_ui._ctrl_on_page", return_value=True), patch.object(
        stub,
        "_compute_rag_display",
        return_value=(fake_stats, "2 / 3 indexed · 2026-01-01 12:00"),
    ):
        asyncio.run(stub._hydrate_rag_status_on_startup())

    assert stub._rag_status_line_value == "2 / 3 indexed · 2026-01-01 12:00"
    assert documents.value == "2 / 3 indexed"
    assert stub._rag_cached_stat_values == fake_stats


def test_present_rag_settings_stats_uses_cache_without_loading() -> None:
    stub = _RagJobUiStub()
    stub._rag_cached_stat_values = {
        "documents": "5 / 5 indexed",
        "index_size": "2 MB",
        "last_indexed": "2026-06-06 10:00",
        "active_chunks": "100",
        "historical_chunks": "0",
    }
    stub._rag_status_line_value = "5 / 5 indexed · 2026-06-06 10:00"
    documents = _MockText("—")
    stub._rag_settings_documents_text = documents

    refresh_calls: list[bool] = []

    def _refresh(*, show_loading: bool = False) -> None:
        refresh_calls.append(show_loading)

    stub._refresh_rag_settings_status = _refresh  # type: ignore[method-assign]

    with patch("iterthink.studio.search_results_ui._ctrl_on_page", return_value=False):
        stub._present_rag_settings_stats()

    assert documents.value == "5 / 5 indexed"
    assert refresh_calls == [False]


def test_rag_stat_label_falls_back_to_default() -> None:
    stub = _RagJobUiStub()
    assert stub._rag_stat_label("documents") == "—"
    stub._rag_cached_stat_values = {"documents": "1 / 1 indexed"}
    assert stub._rag_stat_label("documents") == "1 / 1 indexed"
    assert stub._rag_stat_label("index_size") == "—"


def test_refresh_rag_settings_status_async_runs_on_event_loop_thread() -> None:
    stub = _RagJobUiStub()
    documents = _MockText("—")
    stub._rag_settings_documents_text = documents
    stub.page = SimpleNamespace(web=False, run_task=lambda *a, **k: None)

    fake_stats = {
        "documents": "4 / 4 indexed",
        "index_size": "1 MB",
        "last_indexed": "2026-06-06 12:00",
        "active_chunks": "50",
        "historical_chunks": "0",
    }

    with patch("iterthink.studio.search_results_ui._ctrl_on_page", return_value=True), patch.object(
        stub,
        "_compute_rag_display",
        return_value=(fake_stats, "4 / 4 indexed · 2026-06-06 12:00"),
    ) as compute_mock:
        asyncio.run(stub._refresh_rag_settings_status_async(show_loading=False))

    compute_mock.assert_called_once()
    assert documents.value == "4 / 4 indexed"


def test_rag_progress_status_text() -> None:
    assert MarkdownStudioSearchResults._rag_progress_status_text(current=0, total=0, name="") == "Indexing…"
    assert (
        MarkdownStudioSearchResults._rag_progress_status_text(current=0, total=0, name="a.md")
        == "Indexing — a.md"
    )
    assert (
        MarkdownStudioSearchResults._rag_progress_status_text(current=1, total=3, name="a.md")
        == "Indexing 1 / 3 — a.md"
    )
