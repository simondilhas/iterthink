"""Estimate selection-toolbar anchor from buffer offsets (monospace wrap; no TextField rects)."""

from __future__ import annotations


def _display_width_units(ch: str) -> int:
    if ch == "\t":
        return 4
    return 1


def _soft_wrap_spans_no_newline(segment: str, global_base: int, wrap_cols: int) -> list[tuple[int, int]]:
    """``segment`` contains no ``\\n``. Return half-open global ``[start, end)`` spans for each visual row."""
    out: list[tuple[int, int]] = []
    nloc = len(segment)
    pos = 0
    while pos < nloc:
        line_start = pos
        col = 0
        last_space_break: int | None = None
        j = pos
        while j < nloc:
            ch = segment[j]
            w = _display_width_units(ch)
            if col + w > wrap_cols and col > 0:
                if last_space_break is not None and last_space_break > line_start:
                    le = last_space_break
                    out.append((global_base + line_start, global_base + le))
                    pos = le
                    break
                out.append((global_base + line_start, global_base + j))
                pos = j
                break
            col += w
            if ch == " ":
                last_space_break = j + 1
            j += 1
        else:
            out.append((global_base + line_start, global_base + nloc))
            pos = nloc
    return out


def _all_visual_line_spans(text: str, wrap_cols: int) -> list[tuple[int, int]]:
    """Half-open spans for each visual line (hard ``\\n`` ends a row; soft wrap inside segments)."""
    n = len(text)
    out: list[tuple[int, int]] = []
    base = 0
    while base <= n:
        if base == n:
            break
        nl = text.find("\n", base)
        end = nl if nl != -1 else n
        if end == base and nl != -1:
            out.append((base, base))
            base = nl + 1
            continue
        if end > base:
            out.extend(_soft_wrap_spans_no_newline(text[base:end], base, wrap_cols))
        if nl == -1:
            break
        base = nl + 1
    return out


def _visual_line_index_for_offset(lines: list[tuple[int, int]], text: str, idx: int) -> int:
    """Index of the visual row ``idx`` belongs to (caret on ``\\n`` uses the row above)."""
    n = len(text)
    i = max(0, min(idx, n))
    for k, (a, b) in enumerate(lines):
        if a <= i < b:
            return k
        if a == b == i:
            return k
    if i < n and text[i] == "\n":
        best = 0
        for k, (a, b) in enumerate(lines):
            if a <= i:
                best = k
        return best
    best = 0
    for k, (a, b) in enumerate(lines):
        if a <= i:
            best = k
    return best


def selection_first_line_anchor_px(
    text: str,
    sel_start: int,
    sel_end: int,
    stack_w: float,
    pad_l: float,
    pad_r: float,
    pad_t: float,
    char_w_px: float,
    line_h_px: float,
    *,
    wrap_width_reserve: float = 0.0,
) -> tuple[float, float] | None:
    """
    Return ``(anchor_center_x, line_top_y)`` in stack-local pixels for the first
    document-order line of ``[sel_start, sel_end)``, or ``None`` if invalid.

    ``anchor_center_x`` is the horizontal center of the selected substring clipped
    to that visual line; ``line_top_y`` is the top of that visual text row inside
    the padded content box (stack origin = editor stack top-left).

    ``wrap_width_reserve`` subtracts from usable width before computing ``wrap_cols``
    (scrollbar / caret slop heuristic).
    """
    n = len(text)
    if sel_start < 0 or sel_end > n or sel_start >= sel_end or char_w_px <= 0 or line_h_px <= 0:
        return None
    content_w = max(0.0, float(stack_w) - pad_l - pad_r - float(wrap_width_reserve))
    if content_w <= 0.0:
        return None
    wrap_cols = max(1, int(content_w // char_w_px))

    lines = _all_visual_line_spans(text, wrap_cols)
    if not lines:
        return None

    snap_line = _visual_line_index_for_offset(lines, text, sel_start)
    snap_line_start, snap_line_end = lines[snap_line]

    seg_hi = min(sel_end, snap_line_end)

    def column_at_global_index(pos: int) -> float:
        c = 0.0
        for j in range(snap_line_start, min(pos, n)):
            if text[j] == "\n":
                break
            c += float(_display_width_units(text[j]))
        return c

    c_left = column_at_global_index(sel_start)
    c_right = column_at_global_index(seg_hi)
    line_top_y = pad_t + float(snap_line) * line_h_px
    anchor_x = pad_l + (c_left + c_right) * 0.5 * char_w_px
    return (anchor_x, line_top_y)
