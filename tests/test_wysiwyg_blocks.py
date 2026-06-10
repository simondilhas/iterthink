"""Tests for WYSIWYG block parse/serialize round-trip."""

from __future__ import annotations

from iterthink.studio.wysiwyg_blocks import (
    WysiwygBlock,
    block_at_global_offset,
    global_span_for_block_selection,
    insert_block_after,
    insert_block_at,
    merge_blocks,
    remove_block_at,
    parse_markdown_blocks,
    reorder_blocks,
    serialize_markdown_blocks,
    serialize_single_block,
    set_block_kind,
    split_block_on_enter,
)


def _roundtrip(src: str) -> str:
    blocks = parse_markdown_blocks(src)
    return serialize_markdown_blocks(blocks)


def test_roundtrip_heading_and_paragraph() -> None:
    src = "# Title\n\nBody text."
    out = _roundtrip(src)
    assert "# Title" in out
    assert "Body text." in out
    blocks = parse_markdown_blocks(src)
    assert blocks[0].kind == "heading"
    assert blocks[0].level == 1
    assert blocks[0].text == "Title"
    assert blocks[1].kind == "paragraph"


def test_roundtrip_lists_and_task() -> None:
    src = "- one\n\n- [ ] todo\n\n- [x] done\n\n1. first"
    blocks = parse_markdown_blocks(src)
    kinds = [b.kind for b in blocks]
    assert kinds == ["bullet", "task", "task", "ordered"]
    assert blocks[1].checked is False
    assert blocks[2].checked is True
    out = serialize_markdown_blocks(blocks)
    assert "- [ ]" in out
    assert "- [x]" in out
    assert "1. first" in out


def test_roundtrip_table() -> None:
    src = "Intro.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nOutro."
    blocks = parse_markdown_blocks(src)
    assert any(b.kind == "table" for b in blocks)
    table = next(b for b in blocks if b.kind == "table")
    assert table.rows == [["A", "B"], ["1", "2"]]
    assert table.has_header is True
    out = _roundtrip(src)
    assert "| A | B |" in out
    assert "Intro." in out
    assert "Outro." in out


def test_roundtrip_code_fence() -> None:
    src = "```python\nprint(1)\n```"
    blocks = parse_markdown_blocks(src)
    assert len(blocks) == 1
    assert blocks[0].kind == "code_fence"
    assert blocks[0].language == "python"
    assert blocks[0].text == "print(1)"
    out = _roundtrip(src)
    assert "```python" in out
    assert "print(1)" in out


def test_roundtrip_blockquote() -> None:
    src = "> quote one\n> quote two"
    blocks = parse_markdown_blocks(src)
    assert blocks[0].kind == "blockquote"
    assert "quote one" in blocks[0].text
    assert "quote two" in blocks[0].text


def test_source_spans_cover_document() -> None:
    src = "# H\n\nPara."
    blocks = parse_markdown_blocks(src)
    text = serialize_markdown_blocks(blocks)
    assert blocks[0].source_span[0] == 0
    assert blocks[-1].source_span[1] == len(text)


def test_block_at_global_offset_heading() -> None:
    src = "# Hello"
    blocks = parse_markdown_blocks(src)
    bi, caret = block_at_global_offset(blocks, 2)
    assert bi == 0
    assert caret >= 0


def test_global_span_for_block_selection_second_paragraph() -> None:
    src = "First para.\n\nSecond para with target.\n\nThird."
    blocks = parse_markdown_blocks(src)
    text = serialize_markdown_blocks(blocks)
    block = blocks[1]
    local_start = "Second para with target.".index("target")
    local_end = local_start + len("target")
    g0, g1 = global_span_for_block_selection(block, local_start, local_end)
    assert text[g0:g1] == "target"


def test_split_block_on_enter_heading() -> None:
    block = WysiwygBlock(kind="heading", level=2, text="Hi")
    got = split_block_on_enter(block, 2)
    assert got is not None
    left, right = got
    assert left.kind == "heading"
    assert right.kind == "paragraph"


def test_serialize_single_block_heading() -> None:
    block = WysiwygBlock(kind="heading", level=2, text="Title")
    assert serialize_single_block(block) == "## Title"


def test_reorder_blocks() -> None:
    blocks = [
        WysiwygBlock(kind="paragraph", text="a"),
        WysiwygBlock(kind="paragraph", text="b"),
        WysiwygBlock(kind="paragraph", text="c"),
    ]
    out = reorder_blocks(blocks, 0, 2)
    assert [b.text for b in out] == ["b", "c", "a"]


def test_set_block_kind_to_bullet() -> None:
    block = WysiwygBlock(kind="paragraph", text="item")
    got = set_block_kind(block, "bullet")
    assert got.kind == "bullet"
    assert got.text == "item"


def test_serialize_empty_paragraph_roundtrip() -> None:
    blocks = [
        WysiwygBlock(kind="paragraph", text="One"),
        WysiwygBlock(kind="paragraph", text=""),
        WysiwygBlock(kind="paragraph", text="Two"),
    ]
    md = serialize_markdown_blocks(blocks)
    out = parse_markdown_blocks(md)
    assert len(out) == 3
    assert [b.text for b in out] == ["One", "", "Two"]


def test_remove_block_at() -> None:
    blocks = [
        WysiwygBlock(kind="paragraph", text="a"),
        WysiwygBlock(kind="paragraph", text="b"),
    ]
    out = remove_block_at(blocks, 0)
    assert [b.text for b in out] == ["b"]
    assert remove_block_at([WysiwygBlock(kind="paragraph", text="only")], 0)[0].text == ""


def test_insert_block_at() -> None:
    blocks = [
        WysiwygBlock(kind="paragraph", text="a"),
        WysiwygBlock(kind="paragraph", text="b"),
    ]
    out = insert_block_at(blocks, 0, WysiwygBlock(kind="paragraph", text=""))
    assert [b.text for b in out] == ["", "a", "b"]


def test_insert_block_after() -> None:
    blocks = [
        WysiwygBlock(kind="paragraph", text="a"),
        WysiwygBlock(kind="paragraph", text="b"),
    ]
    out = insert_block_after(blocks, 0, WysiwygBlock(kind="paragraph", text=""))
    assert len(out) == 3
    assert [b.text for b in out] == ["a", "", "b"]


def test_merge_paragraph_blocks() -> None:
    a = WysiwygBlock(kind="paragraph", text="ab")
    b = WysiwygBlock(kind="paragraph", text="cd")
    merged = merge_blocks(a, b)
    assert merged is not None
    assert merged.text == "abcd"
