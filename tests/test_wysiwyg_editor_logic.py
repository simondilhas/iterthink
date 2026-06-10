"""Tests for block preview editor logic."""

from __future__ import annotations

import asyncio

from iterthink.studio.wysiwyg_blocks import WysiwygBlock, merge_blocks, split_block_on_enter
from iterthink.studio.wysiwyg_editor import WysiwygEditorController


def test_split_and_merge_paragraph_blocks() -> None:
    block = WysiwygBlock(kind="paragraph", text="hello")
    got = split_block_on_enter(block, 2)
    assert got is not None
    left, right = got
    assert left.text == "he"
    assert right.text == "llo"
    merged = merge_blocks(left, right)
    assert merged is not None
    assert merged.text == "hello"


def test_preview_controller_parse_and_serialize() -> None:
    captured: list[str] = []

    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("# Title\n\nBody")
    md = ctrl.current_markdown()
    assert "# Title" in md
    assert "Body" in md
    assert len(ctrl.blocks) >= 2
    assert ctrl.editing_block_index is None


def test_start_edit_sets_editing_index() -> None:
    ctrl = WysiwygEditorController(on_markdown_change=lambda _m: None)
    ctrl.sync_from_markdown("One\n\nTwo")
    ctrl.start_edit(1, 0)
    assert ctrl.editing_block_index == 1
    assert ctrl.get_active_field() is not None


def test_commit_edit_clears_editing_index() -> None:
    captured: list[str] = []
    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("Hello")
    ctrl.start_edit(0, 0)
    ctrl.commit_edit()
    assert ctrl.editing_block_index is None
    assert captured


def test_blur_does_not_commit_immediately() -> None:
    captured: list[str] = []
    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("Hello")
    ctrl.start_edit(0, 0)
    ctrl._on_edit_blur(None)  # type: ignore[arg-type]
    assert ctrl.editing_block_index == 0
    assert not captured


def test_chrome_pointer_down_cancels_deferred_blur() -> None:
    captured: list[str] = []
    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("Hello")
    ctrl.start_edit(0, 0)
    ctrl._on_edit_blur(None)  # type: ignore[arg-type]
    ctrl._on_chrome_pointer_down(0)
    assert ctrl.editing_block_index == 0
    assert not captured


def test_set_block_type_preserves_editing_index() -> None:
    ctrl = WysiwygEditorController(on_markdown_change=lambda _m: None)
    ctrl.sync_from_markdown("Hello")
    ctrl.start_edit(0, 0)
    ctrl.set_block_type(0, "heading", level=1)
    assert ctrl.editing_block_index == 0
    assert ctrl.blocks[0].kind == "heading"


def test_insert_paragraph_after_adds_block() -> None:
    captured: list[str] = []
    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("One\n\nTwo")
    ctrl.insert_paragraph_after(0)
    assert len(ctrl.blocks) == 3
    assert ctrl.blocks[1].kind == "paragraph"
    assert ctrl.blocks[1].text == ""
    assert ctrl.editing_block_index == 1
    assert captured
    ctrl.sync_from_markdown(captured[-1])
    assert len(ctrl.blocks) == 3
    assert ctrl.blocks[1].text == ""


def test_delete_block_removes_entry() -> None:
    captured: list[str] = []
    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("One\n\nTwo")
    ctrl.delete_block(0)
    assert len(ctrl.blocks) == 1
    assert ctrl.blocks[0].text == "Two"
    assert captured


def test_reorder_blocks_via_helper() -> None:
    from iterthink.studio.wysiwyg_blocks import reorder_blocks

    blocks = [
        WysiwygBlock(kind="paragraph", text="a"),
        WysiwygBlock(kind="paragraph", text="b"),
    ]
    out = reorder_blocks(blocks, 0, 1)
    assert [b.text for b in out] == ["b", "a"]


def test_deferred_blur_commit_eventually_clears_edit() -> None:
    captured: list[str] = []
    ctrl = WysiwygEditorController(on_markdown_change=captured.append)
    ctrl.sync_from_markdown("Hello")
    ctrl.start_edit(0, 0)
    asyncio.run(ctrl._deferred_blur_commit(ctrl._blur_commit_gen))
    assert ctrl.editing_block_index is None
    assert captured
