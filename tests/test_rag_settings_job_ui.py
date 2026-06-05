"""Tests for persisted RAG job UI state in Settings."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

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

    def _bar_update() -> None:
        bar.updated = True

    def _label_update() -> None:
        label.updated = True

    def _btn_update() -> None:
        btn.updated = True

    bar.update = _bar_update  # type: ignore[method-assign]
    label.update = _label_update  # type: ignore[method-assign]
    btn.update = _btn_update  # type: ignore[method-assign]

    stub._rag_settings_status_line_text = status
    stub._rag_settings_progress_bar = bar
    stub._rag_settings_progress_label = label
    stub._rag_settings_reindex_btn = btn

    with patch("iterthink.studio.search_results_ui._ctrl_on_page", return_value=True):
        stub._apply_rag_job_ui()

    assert status.value == "Indexing 2 / 5 — doc.md"
    assert status.updated is True
    assert bar.visible is True
    assert bar.value == 0.4
    assert label.visible is True
    assert label.value == "2 / 5 — doc.md"
    assert btn.disabled is True


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
