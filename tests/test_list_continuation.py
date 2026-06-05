"""Unit tests for markdown list continuation helpers."""

from __future__ import annotations

import pytest

from iterthink.studio.list_continuation import (
    infer_selection_after_single_enter,
    is_empty_list_item_line,
    map_index_after_normalize_newlines,
    map_norm_index_to_raw,
    markdown_list_continuation_prefix,
    merge_if_list_continuation_after_enter,
    merge_if_list_continuation_at_caret,
    normalize_buffer_newlines,
    plan_list_continuation_after_enter,
    plan_list_continuation_at_caret,
    plan_local_splice,
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
    merged, caret, _ = got
    assert merged == "- first\n- "
    assert caret == len(merged)


def test_merge_happy_path_ordered() -> None:
    old = "intro\n1. one"
    new = "intro\n1. one\n"
    i = len(new) - 1
    got = merge_if_list_continuation_after_enter(old, new, i + 1, i + 1)
    assert got is not None
    merged, caret, _ = got
    assert merged == "intro\n1. one\n2. "
    assert merged[caret - 1] == " "


def test_merge_wrong_caret_still_resolves_when_single_newline_unique() -> None:
    """Insert index comes from the diff; stale TextField selection must not skip merge."""
    old = "- a"
    new = "- a\n"
    want = ("- a\n- ", 6)
    for caret in (0, 2, 4):
        got = merge_if_list_continuation_after_enter(old, new, caret, caret)
        assert got is not None
        assert got[:2] == want


def test_merge_non_collapsed_selection_still_merges_when_newline_diff_unique() -> None:
    """Non-collapsed selection must not block merge when insert index is unambiguous."""
    old = "- a"
    new = "- a\n"
    got = merge_if_list_continuation_after_enter(old, new, 0, 2)
    assert got is not None
    assert got[0] == "- a\n- "


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
    merged, caret, _ = got
    assert merged == "- item\n\n"
    assert caret == 8
    assert merged[caret - 1] == "\n"


def test_merge_empty_bullet_exit_with_trailing_paragraph() -> None:
    """Double Enter on an empty marker must not jump past later paragraphs."""
    old = "Intro\n\n- item\n- \n\nMore text"
    new = old[:16] + "\n" + old[16:]
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "Intro\n\n- item\n\nMore text"
    assert caret == 15
    assert merged[caret:] == "More text"


def test_merge_empty_bullet_exit_stale_eof_caret() -> None:
    old = "Intro\n\n- item\n- "
    new = old + "\n"
    got = merge_if_list_continuation_after_enter(old, new, len(old) + 5, len(old) + 5)
    assert got is not None
    merged, caret, _ = got
    assert merged == "Intro\n\n- item\n\n"
    assert caret == 15


def test_merge_empty_task_exits_list() -> None:
    old = "x\n- [ ] "
    new = "x\n- [ ] \n"
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "x\n\n"
    assert caret == 3


def test_merge_nested_empty_bullet_outdents_wrong_caret() -> None:
    old = "- a\n  - x\n  - "
    new = old + "\n"
    want_merged = "- a\n  - x\n- \n"
    want_caret = 12
    for caret in (0, 5, len(new)):
        got = merge_if_list_continuation_after_enter(old, new, caret, caret)
        assert got is not None
        merged, c, _ = got
        assert merged == want_merged
        assert c == want_caret


def test_merge_nested_empty_then_top_level_exit() -> None:
    """After outdent to top-level empty marker, next Enter exits the list."""
    old = "- a\n  - x\n- "
    new = "- a\n  - x\n- \n"
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "- a\n  - x\n\n"
    assert caret == 11


def test_merge_deep_nested_empty_bullet_outdents() -> None:
    old = "- a\n    - "
    new = old + "\n"
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "- a\n  - \n"
    assert caret == 8


def test_merge_nested_list_first_enter_continues() -> None:
    old = "- a\n  - item"
    new = old + "\n"
    got = merge_if_list_continuation_after_enter(old, new, 1, 1)
    assert got is not None
    merged, caret, _ = got
    assert merged == "- a\n  - item\n  - "
    assert caret == len(merged)


def test_merge_ambiguous_double_newline_buffer_returns_none() -> None:
    """When two positions look like a single inserted \\n, single_newline is None; no merge."""
    assert single_newline_insert_index("\n", "\n\n") is None
    assert merge_if_list_continuation_after_enter("\n", "\n\n", 2, 2) is None


def test_normalize_buffer_newlines_and_index_map() -> None:
    raw = "- a\r\n"
    assert normalize_buffer_newlines(raw) == "- a\n"
    assert map_index_after_normalize_newlines(raw, len(raw)) == len("- a\n")


def test_merge_crlf_bullet_continuation() -> None:
    """Enter after a CRLF-terminated line: normalized diff is a single inserted LF."""
    old = normalize_buffer_newlines("- first")
    new = normalize_buffer_newlines("- first\r\n")
    assert new == "- first\n"
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "- first\n- "
    assert caret == len(merged)


def test_merge_continue_bullet_with_trailing_paragraph() -> None:
    old = "Intro\n\n- item\n\nMore text"
    new = old[:13] + "\n" + old[13:]
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "Intro\n\n- item\n- \n\nMore text"
    assert caret == 16
    assert merged[caret - 2 : caret] == "- "


def test_merge_continue_bullet_selection_none_uses_inferred_caret() -> None:
    old = "Intro\n\n- item\n\nMore text"
    new = old[:13] + "\n" + old[13:]
    ss, se = infer_selection_after_single_enter(old, new)
    got = merge_if_list_continuation_after_enter(old, new, ss, se)
    assert got is not None
    merged, caret, _ = got
    assert merged == "Intro\n\n- item\n- \n\nMore text"
    assert caret == 16


def test_merge_continue_between_list_items() -> None:
    old = "Intro\n\n- item one\n- item two\n\nMore text"
    idx = old.index("- item one") + len("- item one")
    new = old[:idx] + "\n" + old[idx:]
    got = merge_if_list_continuation_after_enter(old, new, len(new), len(new))
    assert got is not None
    merged, caret, _ = got
    assert merged == "Intro\n\n- item one\n- \n- item two\n\nMore text"
    assert caret == 20
    assert merged[caret - 2 : caret] == "- "


def test_merge_three_bullet_continue_stale_eof_caret() -> None:
    """Third list item Enter with trailing body: caret stays on new marker, not EOF."""
    old = "Intro\n\n- one\n- two\n- three\n\nMore text"
    idx = len("Intro\n\n- one\n- two\n- three")
    new = old[:idx] + "\n" + old[idx:]
    got = merge_if_list_continuation_after_enter(old, new, len(new) + 50, len(new) + 50)
    assert got is not None
    merged, caret, insert_i = got
    assert merged == "Intro\n\n- one\n- two\n- three\n- \n\nMore text"
    assert caret == idx + 3
    assert insert_i == idx
    assert merged[caret - 2 : caret] == "- "
    assert merged[caret:] == "\n\nMore text"


def test_map_norm_index_to_raw_crlf() -> None:
    raw = "- a\r\n"
    assert map_norm_index_to_raw(raw, len("- a\n")) == len(raw)


def test_plan_list_continue_after_native_enter() -> None:
    old = "Intro\n\n- one\n- two\n- three\n\nMore text"
    new = old[:12] + "\n" + old[12:]
    plan = plan_list_continuation_after_enter(old, new, 13, 13)
    assert plan is not None
    assert plan.kind == "insert_prefix"
    assert plan.insert_text == "- "
    assert plan.delete_start == plan.delete_end == 13
    assert plan.caret == 15
    assert plan.merged == old[:12] + "\n- " + old[12:]


def test_plan_list_continue_uses_local_insert() -> None:
    old = "Intro\n\n- one\n- two\n- three\n\nMore text"
    idx = len("Intro\n\n- one\n- two\n- three")
    plan = plan_list_continuation_at_caret(old, old, idx)
    assert plan is not None
    assert plan.kind == "insert_prefix"
    assert plan.insert_text == "- "
    assert plan.delete_start == plan.delete_end == idx + 1
    assert plan.caret == idx + 3
    assert plan.merged == old[:idx] + "\n- " + old[idx:]


def test_plan_list_exit_empty_uses_local_splice() -> None:
    prev = "Intro\n\n- item\n- \n\nMore text"
    new = prev[:16] + "\n" + prev[16:]
    plan = plan_list_continuation_after_enter(prev, new, len(new), len(new))
    assert plan is not None
    assert plan.kind == "local_splice"
    assert plan.merged == "Intro\n\n- item\n\nMore text"
    assert plan.caret == 15
    patched = new[: plan.delete_start] + plan.insert_text + new[plan.delete_end :]
    assert patched == plan.merged


def test_plan_local_splice_outdent_nested_empty() -> None:
    new = "- a\n  - x\n  - \n"
    merged = "- a\n  - x\n- \n"
    splice = plan_local_splice(new, merged)
    assert splice is not None
    ds, de, ins = splice
    assert new[:ds] + ins + new[de:] == merged


def test_infer_selection_ambiguous_list_prefers_list_line() -> None:
    old = "Intro\n\n- item\n\nMore text"
    idx = len("Intro\n\n- item")
    new = old[:idx] + "\n" + old[idx:]
    ss, se = infer_selection_after_single_enter(old, new)
    assert ss == se == idx + 1


def test_merge_at_caret_third_bullet_with_trailing() -> None:
    old = "Intro\n\n- one\n- two\n- three\n\nMore text"
    idx = len("Intro\n\n- one\n- two\n- three")
    got = merge_if_list_continuation_at_caret(old, old, idx)
    assert got is not None
    merged, c = got
    assert merged == "Intro\n\n- one\n- two\n- three\n- \n\nMore text"
    assert c == idx + 3
    assert merged[c - 2 : c] == "- "


def test_merge_with_none_selection_uses_diff_only() -> None:
    old = "- a"
    new = "- a\n"
    got = merge_if_list_continuation_after_enter(old, new, 0, 0)
    assert got is not None
    assert got[0] == "- a\n- "
