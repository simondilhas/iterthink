"""Markdown list continuation after Enter (Focus Area compose editor)."""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    with_content = [
        i
        for i in list_related
        if not is_empty_list_item_line(_list_line_before_newline(old, i))
        and markdown_list_continuation_prefix(_list_line_before_newline(old, i)) is not None
    ]
    if len(with_content) == 1:
        return with_content[0]
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


def map_norm_index_to_raw(text: str, norm_idx: int) -> int:
    """Map a normalized offset back to a code-unit index in *text*."""
    norm_idx = max(0, norm_idx)
    n = 0
    i = 0
    while i < len(text):
        if n == norm_idx:
            return i
        if text.startswith("\r\n", i):
            i += 2
            n += 1
        elif text[i] in "\r\n":
            i += 1
            n += 1
        else:
            i += 1
            n += 1
    return len(text)


def resolve_newline_insert_index(
    old: str,
    new: str,
    selection_start: int,
    selection_end: int,
) -> int | None:
    """Resolve the index of the single ``\\n`` inserted between ``old`` and ``new``."""
    i = single_newline_insert_index(old, new)
    if i is not None:
        return i
    i = _resolve_newline_insert_index(old, new, selection_start, selection_end)
    if i is not None:
        return i
    if selection_start != selection_end:
        return None
    return _newline_insert_index_at_caret(old, new, selection_start)


def infer_selection_after_single_enter(old: str, new: str) -> tuple[int, int]:
    """Best-effort collapsed selection when TextField has not reported caret after Enter."""
    ins = single_newline_insert_index(old, new)
    if ins is not None:
        pos = ins + 1
        return pos, pos
    cands = _newline_insert_candidates(old, new)
    if len(cands) == 1:
        pos = cands[0] + 1
        return pos, pos
    if len(cands) > 1:
        resolved = _resolve_newline_insert_index(old, new, len(new), len(new))
        if resolved is not None:
            pos = resolved + 1
            return pos, pos
    return len(new), len(new)


def merge_if_list_continuation_after_enter(
    old: str,
    new: str,
    selection_start: int,
    selection_end: int,
) -> tuple[str, int, int] | None:
    """Handle Enter after a list line: continue list, or exit an empty list item.

    Returns ``(merged_text, caret, insert_index)`` where *insert_index* is the inserted
    ``\\n`` position in *new* (for incremental prefix insertion in the UI).
    """
    # Prefer diff-based index: TextField on_change selection can lag one frame (e.g. still
    # non-collapsed after a word selection) even when the buffer already has exactly one
    # new newline vs ``old`` — do not require a collapsed selection in that case.
    i = resolve_newline_insert_index(old, new, selection_start, selection_end)
    if i is None:
        return None
    line_start = old.rfind("\n", 0, i) + 1
    line = old[line_start:i]
    if is_empty_list_item_line(line):
        outdented = outdent_empty_list_item_line(line)
        if outdented is not None:
            merged = old[:line_start] + outdented + "\n" + old[i:]
            new_caret = line_start + len(outdented)
            return merged, new_caret, i
        merged, new_caret = _merge_exit_empty_list_item(old, line_start, i)
        return merged, new_caret, i
    prefix = markdown_list_continuation_prefix(line)
    if prefix is None:
        return None
    merged = old[:i] + "\n" + prefix + old[i:]
    new_caret = i + 1 + len(prefix)
    return merged, new_caret, i


def merge_if_list_continuation_at_caret(
    old: str,
    buffer: str,
    caret: int,
) -> tuple[str, int] | None:
    """Simulate Enter at *caret* in *buffer* vs snapshot *old*; return merged text and caret."""
    old = normalize_buffer_newlines(old)
    buf = normalize_buffer_newlines(buffer)
    caret = max(0, min(caret, len(buf)))
    new = buf[:caret] + "\n" + buf[caret:]
    got = merge_if_list_continuation_after_enter(old, new, caret + 1, caret + 1)
    if got is None:
        return None
    return got[0], got[1]


def plan_local_splice(current: str, target: str) -> tuple[int, int, str] | None:
    """Return ``(delete_start, delete_end, insert_text)`` when a single local edit transforms *current* into *target*."""
    if current == target:
        return None
    lo = 0
    while lo < len(current) and lo < len(target) and current[lo] == target[lo]:
        lo += 1
    hi_c = len(current)
    hi_t = len(target)
    while hi_c > lo and hi_t > lo and current[hi_c - 1] == target[hi_t - 1]:
        hi_c -= 1
        hi_t -= 1
    insert_text = target[lo:hi_t]
    if current[:lo] + insert_text + current[hi_c:] != target:
        return None
    return lo, hi_c, insert_text


@dataclass(frozen=True)
class ListContinuePlan:
    """How to apply list Enter handling without replacing the whole buffer when possible."""

    kind: str  # "insert_prefix" | "local_splice" | "replace"
    delete_start: int  # normalized start in the current buffer (inclusive)
    delete_end: int  # normalized end in the current buffer (exclusive)
    insert_text: str  # text inserted at delete_start after deleting [delete_start, delete_end)
    merged: str
    caret: int  # normalized caret after edit


def _plan_from_merged(new: str, merged: str, mcaret: int, insert_i: int) -> ListContinuePlan:
    if merged == new:
        return ListContinuePlan("replace", insert_i, insert_i, "", merged, mcaret)
    splice = plan_local_splice(new, merged)
    if splice is not None:
        delete_start, delete_end, insert_text = splice
        if delete_start == delete_end and insert_text:
            kind = "insert_prefix"
        else:
            kind = "local_splice"
        return ListContinuePlan(kind, delete_start, delete_end, insert_text, merged, mcaret)
    return ListContinuePlan("replace", insert_i, insert_i, "", merged, mcaret)


def plan_list_continuation_after_enter(
    old: str,
    new: str,
    selection_start: int,
    selection_end: int,
) -> ListContinuePlan | None:
    """Plan list handling after native Enter (``new`` is ``old`` plus one ``\\n``)."""
    old = normalize_buffer_newlines(old)
    new = normalize_buffer_newlines(new)
    got = merge_if_list_continuation_after_enter(
        old, new, selection_start, selection_end
    )
    if got is None:
        return None
    merged, mcaret, insert_i = got
    return _plan_from_merged(new, merged, mcaret, insert_i)


def plan_list_continuation_at_caret(
    old: str,
    buffer: str,
    caret: int,
) -> ListContinuePlan | None:
    """Plan Enter on a list line; prefer a local insert over a full-buffer replace."""
    old = normalize_buffer_newlines(old)
    buf = normalize_buffer_newlines(buffer)
    caret = max(0, min(caret, len(buf)))
    new = buf[:caret] + "\n" + buf[caret:]
    got = merge_if_list_continuation_after_enter(old, new, caret + 1, caret + 1)
    if got is None:
        return None
    merged, mcaret, insert_i = got
    return _plan_from_merged(new, merged, mcaret, insert_i)
