"""Markdown list continuation after Enter (Focus Area compose editor)."""

from __future__ import annotations

import re

_TASK_LINE = re.compile(r"^(\s*)([-*+])\s+\[[ xX]\]\s*(.*)$")
_ORDERED_LINE = re.compile(r"^(\s*)(\d+)\.\s*(.*)$")
_BULLET_LINE = re.compile(r"^(\s*)([-*+])\s+(?!\[)(.*)$")


def single_newline_insert_index(old: str, new: str) -> int | None:
    """If ``new`` is ``old`` with exactly one ``\\n`` inserted, return that index (unique only)."""
    if len(new) != len(old) + 1:
        return None
    matches = [
        i
        for i, ch in enumerate(new)
        if ch == "\n" and (new[:i] + new[i + 1 :]) == old
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _newline_insert_index_at_caret(old: str, new: str, caret_after: int) -> int | None:
    """Index ``i`` of the inserted ``\\n`` when the caret sits just after it (``caret_after == i + 1``)."""
    if len(new) != len(old) + 1:
        return None
    i = caret_after - 1
    if i < 0 or i >= len(new):
        return None
    if new[i] != "\n":
        return None
    if new[:i] + new[i + 1 :] != old:
        return None
    return i


def _normalize_line(line: str) -> str:
    return line.replace("\r\n", "\n").replace("\r", "\n")


def is_empty_list_item_line(line: str) -> bool:
    """True if ``line`` is only a list marker (bullet, ordered, or task) with no item text."""
    t = _normalize_line(line).rstrip(" \t")
    if not t:
        return False
    if re.fullmatch(r"\s*[-*+]\s+\[[ xX]\]\s*", t):
        return True
    if re.fullmatch(r"\s*\d+\.\s*", t):
        return True
    if re.fullmatch(r"\s*[-*+]\s*", t):
        return True
    return False


_EMPTY_TASK_LINE = re.compile(r"^(\s*)([-*+])\s+\[[ xX]\]\s*$")
_EMPTY_ORDERED_LINE = re.compile(r"^(\s*)(\d+)\.\s*$")
_EMPTY_BULLET_LINE = re.compile(r"^(\s*)([-*+])\s*$")


def _outdent_leading_ws_one_step(indent: str) -> str:
    """One CommonMark-style list level: drop a tab or two spaces from the left."""
    if indent.endswith("\t"):
        return indent[:-1]
    if len(indent) >= 2:
        return indent[:-2]
    if len(indent) == 1:
        return ""
    return indent


def outdent_empty_list_item_line(line: str) -> str | None:
    """
    If ``line`` is a nested empty list marker, return the same marker outdented one step.
    If already top-level, return ``None`` (caller should exit the list instead).
    """
    t = _normalize_line(line).rstrip(" \t")
    m = _EMPTY_TASK_LINE.match(t)
    if m:
        indent, marker = m.group(1), m.group(2)
        if not indent:
            return None
        new_indent = _outdent_leading_ws_one_step(indent)
        return f"{new_indent}{marker} [ ] "
    m = _EMPTY_ORDERED_LINE.match(t)
    if m:
        indent, num_s = m.group(1), m.group(2)
        if not indent:
            return None
        new_indent = _outdent_leading_ws_one_step(indent)
        return f"{new_indent}{num_s}. "
    m = _EMPTY_BULLET_LINE.match(t)
    if m:
        indent, marker = m.group(1), m.group(2)
        if not indent:
            return None
        new_indent = _outdent_leading_ws_one_step(indent)
        return f"{new_indent}{marker} "
    return None


def markdown_list_continuation_prefix(line: str) -> str | None:
    """Return text to insert after ``\\n`` to continue a list, or ``None``."""
    line = _normalize_line(line)
    m = _TASK_LINE.match(line)
    if m:
        indent, marker = m.group(1), m.group(2)
        return f"{indent}{marker} [ ] "
    m = _ORDERED_LINE.match(line)
    if m:
        indent, num_s = m.group(1), m.group(2)
        try:
            n = int(num_s)
        except ValueError:
            return None
        return f"{indent}{n + 1}. "
    m = _BULLET_LINE.match(line)
    if m:
        indent, marker = m.group(1), m.group(2)
        return f"{indent}{marker} "
    return None


def merge_if_list_continuation_after_enter(
    old: str,
    new: str,
    selection_start: int,
    selection_end: int,
) -> tuple[str, int] | None:
    """Handle Enter after a list line: continue list, or exit an empty list item."""
    # Prefer diff-based index: TextField on_change selection can lag one frame (e.g. still
    # non-collapsed after a word selection) even when the buffer already has exactly one
    # new newline vs ``old`` — do not require a collapsed selection in that case.
    i = single_newline_insert_index(old, new)
    if i is None:
        if selection_start != selection_end:
            return None
        i = _newline_insert_index_at_caret(old, new, selection_start)
    if i is None:
        return None
    line_start = old.rfind("\n", 0, i) + 1
    line = old[line_start:i]
    if is_empty_list_item_line(line):
        outdented = outdent_empty_list_item_line(line)
        if outdented is not None:
            merged = old[:line_start] + outdented + "\n" + old[i:]
            new_caret = line_start + len(outdented)
            return merged, new_caret
        merged = old[:line_start] + "\n" + old[i:]
        new_caret = line_start + 1
        return merged, new_caret
    prefix = markdown_list_continuation_prefix(line)
    if prefix is None:
        return None
    merged = old[:i] + "\n" + prefix + old[i:]
    new_caret = i + 1 + len(prefix)
    return merged, new_caret
