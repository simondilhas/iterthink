"""Tests for Focus Area wysiwyg/source compose mode helpers."""

from __future__ import annotations

import flet as ft

from iterthink.studio.focus_area import MarkdownStudioCompose
from iterthink.studio.wysiwyg_editor import WysiwygEditorController


class _ComposeStub(MarkdownStudioCompose):
    def __init__(self) -> None:
        self._main_tab_index = 1
        self._focus_view_mode = "wysiwyg"
        self.current_path = None
        self._compose_plan_viewer_active = lambda: False
        self.editor = ft.TextField(value="")
        self._compose_sel_span = None
        self._wysiwyg_controller = None

    def _document_pdf_profile(self):
        return None

    def _editor_buffer(self) -> str:
        return self.editor.value or ""


def test_wysiwyg_unavailable_without_note() -> None:
    stub = _ComposeStub()
    assert stub._compose_wysiwyg_available() is False


def test_wysiwyg_available_for_md_note() -> None:
    from pathlib import Path

    stub = _ComposeStub()
    stub.current_path = Path("/tmp/note.md")
    assert stub._compose_wysiwyg_available() is True


def test_compose_global_selection_maps_wysiwyg_block_to_document() -> None:
    src = "First para.\n\nSecond para with target.\n\nThird."
    stub = _ComposeStub()
    stub.editor.value = src
    ctrl = WysiwygEditorController(on_markdown_change=lambda _md: None)
    ctrl.sync_from_markdown(src)
    ctrl.start_edit(1, 0)
    assert ctrl._edit_field is not None
    ctrl._edit_field.value = "Second para with target."
    body = "Second para with target."
    local_start = body.index("target")
    local_end = local_start + len("target")
    ctrl._edit_field.selection = ft.TextSelection(local_start, local_end)
    stub._wysiwyg_controller = ctrl
    stub._compose_sel_span = (local_start, local_end)

    gspan = stub._compose_global_selection_range()
    assert gspan is not None
    assert src[gspan[0] : gspan[1]] == "target"
    assert src[stub._compose_active_paragraph_offset() : gspan[1]] == "target"


def test_compose_active_paragraph_offset_uses_editing_block_without_selection() -> None:
    src = "First para.\n\nSecond para.\n\nThird."
    stub = _ComposeStub()
    stub.editor.value = src
    ctrl = WysiwygEditorController(on_markdown_change=lambda _md: None)
    ctrl.sync_from_markdown(src)
    ctrl.start_edit(2, 0)
    stub._wysiwyg_controller = ctrl

    off = stub._compose_active_paragraph_offset()
    assert src[off:].startswith("Third.")
