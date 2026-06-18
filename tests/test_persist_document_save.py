"""Tests for off-thread document persistence used by save_file."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from iterthink.studio.markdown_studio import _persist_document_save_sync


def test_persist_document_save_sync_writes_disk_and_updates_db(tmp_path: Path) -> None:
    md = tmp_path / "note.md"
    md.write_text("old", encoding="utf-8")
    with patch("iterthink.studio.markdown_studio.session_scope") as scope:
        session = MagicMock()
        scope.return_value.__enter__.return_value = session
        _persist_document_save_sync(
            md,
            "new body",
            persist_snapshot=False,
            reason="manual",
            version_display_label=None,
        )
    assert md.read_text(encoding="utf-8") == "new body"
    assert scope.call_count == 1


def test_save_file_uses_to_thread(tmp_path: Path) -> None:
    from iterthink.studio.markdown_studio import MarkdownStudio

    studio = MarkdownStudio.__new__(MarkdownStudio)
    md = tmp_path / "note.md"
    md.write_text("draft", encoding="utf-8")
    studio.current_path = md
    studio.last_saved_text = ""
    studio._flush_review_edits_if_changed = MagicMock()
    studio._working_document_text = MagicMock(return_value="draft")
    studio.schedule_rag_reindex = MagicMock()
    studio._refresh_compare_tab_candidate_ui = MagicMock()
    studio._refresh_title_bar = MagicMock()
    studio._main_tab_index = 1
    studio._margin_gen = 0
    studio.page = MagicMock(run_task=MagicMock())
    studio._snack = MagicMock()

    with patch(
        "iterthink.studio.markdown_studio.asyncio.to_thread",
        new_callable=MagicMock,
    ) as to_thread:
        to_thread.return_value = asyncio.sleep(0)

        async def _run() -> None:
            await studio.save_file(silent=True, persist_snapshot=False)

        asyncio.run(_run())
        to_thread.assert_called_once()
        assert studio.last_saved_text == "draft"
