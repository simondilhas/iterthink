"""Window math for virtualized History / Review paragraph compare ListViews."""

from __future__ import annotations

COMPARE_VIRTUAL_MIN_ROWS = 40
COMPARE_VIRTUAL_ROW_HEIGHT_PX = 52.0
COMPARE_VIRTUAL_OVERSCAN_ROWS = 6


def compare_should_virtualize(display_row_count: int) -> bool:
    return display_row_count >= COMPARE_VIRTUAL_MIN_ROWS


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
