"""Window math for virtualized History / Review paragraph compare ListViews."""

from __future__ import annotations

import bisect
from typing import Any

import flet as ft

from iterthink.compare.margin import wrapped_line_count
from iterthink.studio.constants import (
    COMPARE_ACTION_COL_W,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
    COMPARE_EVAL_COL_W,
    COMPARE_PILL_COL_W,
)

COMPARE_VIRTUAL_MIN_ROWS = 40
COMPARE_VIRTUAL_ROW_HEIGHT_PX = 52.0
COMPARE_VIRTUAL_OVERSCAN_ROWS = 6
COMPARE_VIRTUAL_CELL_PAD_V_PX = 16.0
COMPARE_VIRTUAL_LINE_PX = float(COMPARE_COL_FONT_SIZE) * float(COMPARE_COL_LINE_HEIGHT)
COMPARE_VIRTUAL_HEIGHT_REFINE_THRESHOLD_PX = 8.0


def compare_should_virtualize(display_row_count: int) -> bool:
    del display_row_count  # progressive loading disabled; re-enable via COMPARE_VIRTUAL_MIN_ROWS later
    return False


def compare_row_scroll_key(cand_idx: int) -> str:
    """Stable ListView ``scroll_key`` for a candidate paragraph compare row."""
    return f"compare_para_{int(cand_idx)}"


def compare_row_scroll_ft_key(cand_idx: int) -> ft.ScrollKey:
    """Flet scroll target key for a candidate paragraph compare row."""
    return ft.ScrollKey(compare_row_scroll_key(cand_idx))


def compare_text_column_width(
    listview_width: float,
    *,
    show_actions: bool = True,
) -> float:
    """Approximate width of one text column in the compare row grid."""
    fixed = (
        float(COMPARE_EVAL_COL_W)
        + float(COMPARE_PILL_COL_W)
        + (float(COMPARE_ACTION_COL_W) if show_actions else 0.0)
        + 20.0
    )
    remaining = max(80.0, float(listview_width) - fixed)
    return remaining / 2.0


def compare_estimated_row_height(
    old_text: str,
    new_text: str,
    *,
    content_width: float,
    min_height: float = COMPARE_VIRTUAL_ROW_HEIGHT_PX,
) -> float:
    """Estimate rendered compare-row height from wrapped line counts."""
    lines = max(
        wrapped_line_count(old_text or "", content_width),
        wrapped_line_count(new_text or "", content_width),
    )
    return max(min_height, float(lines) * COMPARE_VIRTUAL_LINE_PX + COMPARE_VIRTUAL_CELL_PAD_V_PX)


def compare_build_row_heights(
    display_rows: list[Any],
    *,
    content_width: float,
    text_single: bool = False,
    field_meta: list[Any] | None = None,
) -> list[float]:
    """One height per ``display_rows`` index (aligned with mount loop ``di``)."""
    heights: list[float] = []
    field_i = 0
    for row in display_rows:
        if getattr(row, "row_type", None) == "ghost_moved" and text_single:
            heights.append(0.0)
            continue
        old = getattr(row, "old_text", "") or ""
        row_type = getattr(row, "row_type", "")
        if row_type in ("ghost_moved", "removed"):
            new = ""
        elif field_meta is not None and field_i < len(field_meta):
            meta = field_meta[field_i]
            new = getattr(meta, "text", None) or getattr(row, "new_text", "") or ""
            field_i += 1
        else:
            new = getattr(row, "new_text", "") or ""
        heights.append(
            compare_estimated_row_height(old, new, content_width=content_width)
        )
    return heights


def compare_prefix_offsets(heights: list[float]) -> list[float]:
    """``prefix[i]`` = cumulative Y before display row ``i``; ``prefix[n]`` = total height."""
    prefix = [0.0]
    for h in heights:
        prefix.append(prefix[-1] + max(0.0, float(h)))
    return prefix


def compare_visible_window_for_heights(
    *,
    scroll_offset: float,
    heights: list[float],
    viewport_height: float,
    overscan: int = COMPARE_VIRTUAL_OVERSCAN_ROWS,
) -> tuple[int, int]:
    """Return ``(first_inclusive, last_exclusive)`` using variable row heights."""
    n = len(heights)
    if n <= 0:
        return 0, 0
    prefix = compare_prefix_offsets(heights)
    first_raw = max(0, bisect.bisect_right(prefix, float(scroll_offset)) - 1)
    first = max(0, first_raw - overscan)
    bottom = float(scroll_offset) + float(viewport_height)
    last_raw = bisect.bisect_left(prefix, bottom)
    last = min(n, last_raw + overscan + 1)
    if last <= first:
        last = min(n, first + 1)
    return first, last


def compare_spacer_heights(
    first: int,
    last: int,
    heights: list[float],
) -> tuple[float, float]:
    """Top and tail spacer pixel heights for a virtual window."""
    top = sum(max(0.0, float(h)) for h in heights[:first])
    tail = sum(max(0.0, float(h)) for h in heights[last:])
    return top, tail


def compare_scroll_offset_for_row(
    display_index: int,
    heights: list[float],
    *,
    margin: float = 24.0,
) -> float:
    """Scroll offset to bring ``display_index`` near the top of the viewport."""
    if display_index <= 0:
        return 0.0
    idx = min(int(display_index), len(heights))
    return max(0.0, sum(max(0.0, float(h)) for h in heights[:idx]) - margin)


def compare_visible_window(
    *,
    scroll_offset: float,
    viewport_height: float,
    total_rows: int,
    row_height: float = COMPARE_VIRTUAL_ROW_HEIGHT_PX,
    overscan: int = COMPARE_VIRTUAL_OVERSCAN_ROWS,
) -> tuple[int, int]:
    """Return ``(first_inclusive, last_exclusive)`` display-row indices to mount."""
    if total_rows <= 0:
        return 0, 0
    first = max(0, int(scroll_offset / row_height) - overscan)
    visible = max(1, int(viewport_height / row_height) + 2 * overscan)
    last = min(total_rows, first + visible)
    if last <= first:
        last = min(total_rows, first + 1)
    return first, last


def display_index_for_cand_idx(
    cand_idx: int,
    *,
    eval_cand_indices: list[int | None] | None,
    comp_display_index: dict[int, int],
    display_rows: list[Any] | None = None,
) -> int:
    """Map candidate paragraph index → display-row index for virtual compare scroll."""
    comp_idx: int | None = None
    if eval_cand_indices:
        for i, ci in enumerate(eval_cand_indices):
            if ci == cand_idx:
                comp_idx = int(i)
                break
    if comp_idx is None:
        comp_idx = int(cand_idx)
    mapped = comp_display_index.get(comp_idx)
    if mapped is not None:
        return int(mapped)
    if display_rows:
        for di, row in enumerate(display_rows):
            if getattr(row, "row_type", None) != "comparison":
                continue
            if int(getattr(row, "new_paragraph_index", -1)) == int(cand_idx):
                return int(di)
    return int(cand_idx)


def build_future_comp_display_index(
    display_rows: list[Any],
    *,
    text_single: bool,
) -> dict[int, int]:
    """Map comparison ``comp_idx`` → ``display_rows`` index for scroll-aware overlay Y."""
    out: dict[int, int] = {}
    comp_idx = 0
    for di, row in enumerate(display_rows):
        if row.row_type == "ghost_moved" and text_single:
            continue
        if row.row_type == "comparison":
            out[comp_idx] = di
            comp_idx += 1
    return out


def result_card_top_for_row(
    *,
    display_index: int,
    scroll_offset: float,
    stack_height: float,
    card_max_height: float,
    row_height: float = COMPARE_VIRTUAL_ROW_HEIGHT_PX,
    list_padding: float = 4.0,
    margin: float = 4.0,
) -> float:
    """Stack-local ``top`` for the Analyse result card beside a compare row."""
    top = float(display_index) * row_height - scroll_offset + list_padding
    max_top = max(margin, stack_height - card_max_height - margin)
    return max(margin, min(top, max_top))


def listview_y_before_control(
    controls: list[Any],
    target: Any,
    measured_heights: dict[int, float],
    *,
    default_row_height: float = COMPARE_VIRTUAL_ROW_HEIGHT_PX,
) -> float | None:
    """Sum ListView child heights before ``target``; ``measured_heights`` keyed by ``id(control)``."""
    try:
        idx = controls.index(target)
    except ValueError:
        return None
    y = 0.0
    for c in controls[:idx]:
        ch = float(getattr(c, "height", 0) or 0)
        if ch <= 0:
            ch = measured_heights.get(id(c), default_row_height)
        y += ch
    return y


def result_card_top_beside_row(
    *,
    row_y: float,
    scroll_offset: float,
    list_padding_top: float,
    stack_height: float,
    card_max_height: float,
    margin: float = 4.0,
) -> float:
    """Stack-local ``top`` aligned to a measured compare row (not a fixed row-height estimate)."""
    top = float(row_y) - float(scroll_offset) + float(list_padding_top)
    max_top = max(margin, stack_height - card_max_height - margin)
    return max(margin, min(top, max_top))


def result_card_left_beside_eval(eval_col_width: float, gap: float = 4.0) -> float:
    """Stack-local ``left`` immediately right of the eval / Analyse symbol column."""
    return float(eval_col_width) + float(gap)
