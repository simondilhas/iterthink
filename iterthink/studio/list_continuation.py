"""Markdown list continuation after Enter (Focus Area compose editor)."""

from __future__ import annotations

import re

_TASK_LINE = re.compile(r"^(\s*)([-*+])\s+\[[ xX]\]\s*(.*)$")
_ORDERED_LINE = re.compile(r"^(\s*)(\d+)\.\s*(.*)$")
_BULLET_LINE = re.compile(r"^(\s*)([-*+])\s+(?!\[)(.*)$")


def _newline_insert_candidates(old: str, new: str) -> list[int]:
    """Indices in ``new`` where removing ``\\n`` at that position yields ``old``."""
    if len(new) != len(old) + 1:
        return []
    return [
        i
        for i, ch in enumerate(new)
        if ch == "\n" and (new[:i] + new[i + 1 :]) == old
    ]


def single_newline_insert_index(old: str, new: str) -> int | None:
    """If ``new`` is ``old`` with exactly one ``\\n`` inserted, return that index (unique only)."""
    matches = _newline_insert_candidates(old, new)
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


def normalize_buffer_newlines(text: str) -> str:
    """Whole-buffer CRLF/CR → LF for diff-based Enter merge."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def map_index_after_normalize_newlines(text: str, idx: int) -> int:
    """Map a code-unit offset in *text* to the same logical position after ``normalize_buffer_newlines``."""
    idx = max(0, min(idx, len(text)))
    return len(text[:idx].replace("\r\n", "\n").replace("\r", "\n"))


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


def _list_line_before_newline(old: str, i: int) -> str:
    line_start = old.rfind("\n", 0, i) + 1
    return old[line_start:i]


def _resolve_newline_insert_index(
    old: str,
    new: str,
    selection_start: int,
    selection_end: int,
) -> int | None:
    """Pick the inserted ``\\n`` when diff or caret alone is ambiguous (e.g. near ``\\n\\n``)."""
    candidates = _newline_insert_candidates(old, new)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    list_related = [
        i
        for i in candidates
        if is_empty_list_item_line(_list_line_before_newline(old, i))
        or markdown_list_continuation_prefix(_list_line_before_newline(old, i)) is not None
    ]
    if len(list_related) == 1:
        return list_related[0]
    pool = list_related or candidates
    if selection_start != selection_end:
        return pool[0]
    preferred = selection_start - 1
    if preferred in pool:
        return preferred
    return min(pool, key=lambda j: abs(j - selection_start))


def _merge_exit_empty_list_item(old: str, line_start: int, i: int) -> tuple[str, int]:
    """Drop an empty top-level list marker and leave the caret on the following blank line."""
    rest = i
    if rest < len(old) and old[rest] == "\n":
        rest += 1
    if rest >= len(old):
        merged = old[:line_start] + "\n"
    else:
        merged = old[:line_start] + old[rest:]
    return merged, line_start + 1


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
        i = _resolve_newline_insert_index(old, new, selection_start, selection_end)
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
        return _merge_exit_empty_list_item(old, line_start, i)
    prefix = markdown_list_continuation_prefix(line)
    if prefix is None:
        return None
    merged = old[:i] + "\n" + prefix + old[i:]
    new_caret = i + 1 + len(prefix)
    return merged, new_caret
