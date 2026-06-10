"""Tests for wysiwyg block spacing and scrollbar inset constants."""

from __future__ import annotations

from iterthink.studio.constants import (
    COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX,
    COMPOSE_EDITOR_LINE_HEIGHT_PX,
    COMPOSE_EDITOR_SCROLLBAR_INSET_PX,
    COMPOSE_EDITOR_SCROLLBAR_TRACK_PX,
    COMPOSE_PREVIEW_BLOCK_GAP_PX,
)
from iterthink.studio.wysiwyg_blocks import WysiwygBlock
from iterthink.studio.wysiwyg_editor import block_gap_after


def test_scrollbar_inset_matches_content_pad_plus_track() -> None:
    assert COMPOSE_EDITOR_SCROLLBAR_INSET_PX == (
        COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX + COMPOSE_EDITOR_SCROLLBAR_TRACK_PX
    )


def test_block_gap_after_adjacent_lists_is_zero() -> None:
    bullet = WysiwygBlock(kind="bullet", text="a")
    task = WysiwygBlock(kind="task", text="b")
    ordered = WysiwygBlock(kind="ordered", text="c", order_num=1)
    assert block_gap_after(bullet, task) == 0.0
    assert block_gap_after(task, ordered) == 0.0


def test_block_gap_after_paragraph_pair_is_one_line() -> None:
    a = WysiwygBlock(kind="paragraph", text="First.")
    b = WysiwygBlock(kind="paragraph", text="Second.")
    assert block_gap_after(a, b) == float(COMPOSE_EDITOR_LINE_HEIGHT_PX)


def test_block_gap_after_list_to_paragraph_is_tight_block_gap() -> None:
    bullet = WysiwygBlock(kind="bullet", text="item")
    para = WysiwygBlock(kind="paragraph", text="Body.")
    assert block_gap_after(bullet, para) == float(COMPOSE_PREVIEW_BLOCK_GAP_PX)


def test_block_gap_after_last_block_is_zero() -> None:
    para = WysiwygBlock(kind="paragraph", text="Last.")
    assert block_gap_after(para, None) == 0.0
