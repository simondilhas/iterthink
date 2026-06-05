"""Pure string helpers for compose selection toolbar (markdown wrap, lists, indent)."""

from __future__ import annotations

import re

_NUMBERED_PREFIX = re.compile(r"^(\s*)(\d+)\.\s(.*)$")
# Line is already a GFM task item (body after ] may start with spaces).
_TASK_LINE = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+\[([ xX])\](.*)$")


def expand_selection_to_line_bounds(text: str, a: int, b: int) -> tuple[int, int]:
    """Expand [a, b) to full lines (from char after previous newline through newline ending last line, or EOF)."""
    n = len(text)
    a = max(0, min(n, a))
    b = max(0, min(n, b))
    if b < a:
        a, b = b, a
    line_start = text.rfind("\n", 0, a) + 1
    nl = text.find("\n", b)
    line_end = n if nl == -1 else nl + 1
    return line_start, line_end


def apply_bold_wrap(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    """Wrap selection in ** or unwrap if delimiters are in or around the selection."""
    n = len(text)
    if a < 0 or b > n or a >= b:
        return None
    sel = text[a:b]
    if sel.startswith("**") and sel.endswith("**") and len(sel) >= 4:
        inner = sel[2:-2]
        new_t = text[:a] + inner + text[b:]
        return new_t, a, a + len(inner)
    if a >= 2 and b + 2 <= n and text[a - 2 : a] == "**" and text[b : b + 2] == "**":
        inner = sel
        new_t = text[: a - 2] + inner + text[b + 2 :]
        return new_t, a - 2, a - 2 + len(inner)
    new_t = text[:a] + "**" + sel + "**" + text[b:]
    return new_t, a, a + len(sel) + 4


def apply_italic_wrap(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    """Wrap selection in *italic* or unwrap one *…* layer (not **bold**)."""
    n = len(text)
    if a < 0 or b > n or a >= b:
        return None
    sel = text[a:b]
    if (
        len(sel) >= 2
        and sel[0] == "*"
        and sel[-1] == "*"
        and not sel.startswith("**")
        and not sel.endswith("**")
    ):
        inner = sel[1:-1]
        new_t = text[:a] + inner + text[b:]
        return new_t, a, a + len(inner)
    if (
        a >= 1
        and b + 1 <= n
        and text[a - 1] == "*"
        and text[b : b + 1] == "*"
        and (a < 2 or text[a - 2] != "*")
        and (b + 1 >= n or text[b + 1] != "*")
    ):
        inner = sel
        new_t = text[: a - 1] + inner + text[b + 1 :]
        return new_t, a - 1, a - 1 + len(inner)
    new_t = text[:a] + "*" + sel + "*" + text[b:]
    return new_t, a, a + len(sel) + 2


def _prefix_bullet_line(line: str) -> str:
    """Add GFM '- ' after leading whitespace if not already a bullet/numbered/task line."""
    m = re.match(r"^(\s*)(.*)$", line)
    if m is None:
        return "- " + line
    ws, rest = m.group(1), m.group(2)
    if not rest:
        return ws + "- "
    if rest.startswith(("- ", "* ", "+ ")) or _NUMBERED_PREFIX.match(rest):
        return line
    return ws + "- " + rest


def apply_bullet_block(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    """Prefix each line in line-expanded range with '- ' when missing."""
    ls, le = expand_selection_to_line_bounds(text, a, b)
    n = len(text)
    if ls < 0 or le > n or ls >= le:
        return None
    block = text[ls:le]
    lines = block.splitlines(keepends=True)
    if not lines:
        return None
    out_parts: list[str] = []
    for piece in lines:
        if piece.endswith("\r\n"):
            core, nl = piece[:-2], "\r\n"
        elif piece.endswith("\n"):
            core, nl = piece[:-1], "\n"
        elif piece.endswith("\r"):
            core, nl = piece[:-1], "\r"
        else:
            core, nl = piece, ""
        out_parts.append(_prefix_bullet_line(core) + nl)
    new_block = "".join(out_parts)
    new_text = text[:ls] + new_block + text[le:]
    block_end = ls + len(new_block)
    return new_text, ls, block_end


def apply_numbered_block(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    """Replace line-expanded block with numbered list 1. 2. … (strip old list markers)."""
    ls, le = expand_selection_to_line_bounds(text, a, b)
    n = len(text)
    if ls < 0 or le > n or ls >= le:
        return None
    block = text[ls:le]
    lines = block.splitlines(keepends=True)
    if not lines:
        return None
    out_parts: list[str] = []
    idx = 1
    for piece in lines:
        if piece.endswith("\r\n"):
            core, nl = piece[:-2], "\r\n"
        elif piece.endswith("\n"):
            core, nl = piece[:-1], "\n"
        elif piece.endswith("\r"):
            core, nl = piece[:-1], "\r"
        else:
            core, nl = piece, ""
        m = re.match(r"^(\s*)(.*)$", core)
        if not m:
            out_parts.append(f"{idx}. " + core + nl)
        else:
            ws, rest = m.group(1), m.group(2)
            nm = _NUMBERED_PREFIX.match(rest)
            if nm:
                rest = nm.group(3)
            elif rest.startswith(("- ", "* ", "+ ")):
                rest = rest[2:]
            out_parts.append(ws + f"{idx}. " + rest + nl)
        idx += 1
    new_block = "".join(out_parts)
    new_text = text[:ls] + new_block + text[le:]
    block_end = ls + len(new_block)
    return new_text, ls, block_end


def _prefix_checklist_line(line: str) -> str:
    """Prefix line with GFM ``- [ ] `` when not already a task item; strip plain bullet/number."""
    m = re.match(r"^(\s*)(.*)$", line)
    if m is None:
        return "- [ ] " + line
    ws, rest = m.group(1), m.group(2)
    if not rest:
        return ws + "- [ ] "
    if _TASK_LINE.match(line):
        return line
    if rest.startswith(("- ", "* ", "+ ")):
        return ws + "- [ ] " + rest[2:]
    nm = _NUMBERED_PREFIX.match(rest)
    if nm:
        return ws + "- [ ] " + nm.group(3)
    return ws + "- [ ] " + rest


def apply_checklist_block(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    """Prefix each line in line-expanded range with GFM ``- [ ] `` when not already a task."""
    ls, le = expand_selection_to_line_bounds(text, a, b)
    n = len(text)
    if ls < 0 or le > n or ls >= le:
        return None
    block = text[ls:le]
    lines = block.splitlines(keepends=True)
    if not lines:
        return None
    out_parts: list[str] = []
    for piece in lines:
        if piece.endswith("\r\n"):
            core, nl = piece[:-2], "\r\n"
        elif piece.endswith("\n"):
            core, nl = piece[:-1], "\n"
        elif piece.endswith("\r"):
            core, nl = piece[:-1], "\r"
        else:
            core, nl = piece, ""
        out_parts.append(_prefix_checklist_line(core) + nl)
    new_block = "".join(out_parts)
    new_text = text[:ls] + new_block + text[le:]
    block_end = ls + len(new_block)
    return new_text, ls, block_end


def _outdent_line_prefix(line: str) -> str:
    if line.startswith("\t"):
        return line[1:]
    if line.startswith("  "):
        return line[2:]
    if line.startswith(" "):
        return line[1:]
    return line


def _indent_line_prefix(line: str) -> str:
    return "  " + line


def apply_indent_block(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    ls, le = expand_selection_to_line_bounds(text, a, b)
    n = len(text)
    if ls < 0 or le > n or ls >= le:
        return None
    block = text[ls:le]
    lines = block.splitlines(keepends=True)
    out_parts: list[str] = []
    for piece in lines:
        if piece.endswith("\r\n"):
            core, nl = piece[:-2], "\r\n"
        elif piece.endswith("\n"):
            core, nl = piece[:-1], "\n"
        elif piece.endswith("\r"):
            core, nl = piece[:-1], "\r"
        else:
            core, nl = piece, ""
        out_parts.append(_indent_line_prefix(core) + nl)
    new_block = "".join(out_parts)
    new_text = text[:ls] + new_block + text[le:]
    block_end = ls + len(new_block)
    return new_text, ls, block_end


def apply_outdent_block(text: str, a: int, b: int) -> tuple[str, int, int] | None:
    ls, le = expand_selection_to_line_bounds(text, a, b)
    n = len(text)
    if ls < 0 or le > n or ls >= le:
        return None
    block = text[ls:le]
    lines = block.splitlines(keepends=True)
    out_parts: list[str] = []
    for piece in lines:
        if piece.endswith("\r\n"):
            core, nl = piece[:-2], "\r\n"
        elif piece.endswith("\n"):
            core, nl = piece[:-1], "\n"
        elif piece.endswith("\r"):
            core, nl = piece[:-1], "\r"
        else:
            core, nl = piece, ""
        out_parts.append(_outdent_line_prefix(core) + nl)
    new_block = "".join(out_parts)
    new_text = text[:ls] + new_block + text[le:]
    block_end = ls + len(new_block)
    return new_text, ls, block_end
