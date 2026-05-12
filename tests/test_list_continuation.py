"""Unit tests for markdown list continuation helpers."""

from __future__ import annotations

import pytest

from iterthink.studio.list_continuation import (
    is_empty_list_item_line,
    markdown_list_continuation_prefix,
    merge_if_list_continuation_after_enter,
    single_newline_insert_index,
)


@pytest.mark.parametrize(
    ("old", "new", "want"),
    [
        ("ab", "a\nb", 1),
        ("", "\n", 0),
        ("a", "\na", 0),
        ("hello", "hel\nlo", 3),
    ],
)
def test_single_newline_insert_index_ok(old: str, new: str, want: int) -> None:
    assert single_newline_insert_index(old, new) == want


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("a", "a"),
        ("a", "ab"),
        ("a", "a\n\n"),
        ("ab", "a\n\nb"),
        ("x\ny", "x\n\ny"),
    ],
)
def test_single_newline_insert_index_none(old: str, new: str) -> None:
    assert single_newline_insert_index(old, new) is None


@pytest.mark.parametrize(
    ("line", "prefix"),
    [
        ("- foo", "- "),
        ("* bar", "* "),
        ("+ baz", "+ "),
        ("  - x", "  - "),
        ("\t* y", "\t* "),
        ("1. first", "2. "),
        ("12. twelve", "13. "),
        ("  3. nested", "  4. "),
        ("- [ ] todo", "- [ ] "),
        ("- [x] done", "- [ ] "),
        ("* [X] cap", "* [ ] "),
    ],
)
def test_markdown_list_continuation_prefix(line: str, prefix: str) -> None:
    assert markdown_list_continuation_prefix(line) == prefix


@pytest.mark.parametrize(
    "line",
    [
        "",
        "plain",
        "not. a list",
        "1) paren",
        "-[ ] no space before bracket",
    ],
)
def test_markdown_list_continuation_prefix_none(line: str) -> None:
    assert markdown_list_continuation_prefix(line) is None


def test_merge_happy_path_bullet() -> None:
    old = "- first"
    new = "- first\n"
    got = merge_if_list_continuation_after_enter(old, new, 8, 8)
    assert got is not None
    merged, caret = got
    assert merged == "- first\n- "
    assert caret == len(merged)


def test_merge_happy_path_ordered() -> None:
    old = "intro\n1. one"
    new = "intro\n1. one\n"
    i = len(new) - 1
    got = merge_if_list_continuation_after_enter(old, new, i + 1, i + 1)
    assert got is not None
    merged, caret = got
    assert merged == "intro\n1. one\n2. "
    assert merged[caret - 1] == " "


def test_merge_wrong_caret_returns_none() -> None:
    old = "- a"
    new = "- a\n"
    assert merge_if_list_continuation_after_enter(old, new, 0, 0) is None
    assert merge_if_list_continuation_after_enter(old, new, 3, 3) is None


def test_merge_non_collapsed_selection() -> None:
    old = "- a"
    new = "- a\n"
    assert merge_if_list_continuation_after_enter(old, new, 0, 2) is None


def test_merge_double_newline() -> None:
    old = "- a"
    new = "- a\n\n"
    assert merge_if_list_continuation_after_enter(old, new, 5, 5) is None


@pytest.mark.parametrize(
    "line",
    [
        "- ",
        "  - ",
        "- [ ] ",
        "- [x]",
        "1. ",
        "  2.  ",
    ],
)
def test_is_empty_list_item_line_true(line: str) -> None:
    assert is_empty_list_item_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "",
        " ",
        "- a",
        "- [ ] x",
        "1. one",
        "plain",
    ],
)
def test_is_empty_list_item_line_false(line: str) -> None:
    assert not is_empty_list_item_line(line)


def test_merge_empty_bullet_second_enter_exits_list() -> None:
    old = "- item\n- "
    new = "- item\n- \n"
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret = got
    assert merged == "- item\n\n"
    assert caret == 8
    assert merged[caret - 1] == "\n"


def test_merge_empty_task_exits_list() -> None:
    old = "x\n- [ ] "
    new = "x\n- [ ] \n"
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret = got
    assert merged == "x\n\n"
    assert caret == 3
