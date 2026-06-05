"""Read-only markdown preview tweaks (e.g. task lists as visible checkboxes)."""

from __future__ import annotations

import re

import flet as ft

from . import ui_theme
from .constants import COMPOSE_PREVIEW_BLOCK_GAP_PX
from .markdown_tables import (
    build_table_preview,
    compute_column_widths_px,
    split_markdown_with_tables,
)

# GFM task items: optional indent + list marker + "[ ]" / "[x]" + body (body may start with spaces).
_TASK_ITEM_LINE = re.compile(
    r"^(\s*)(?:[-*+]|\d+\.)\s+\[([ xX])\](.*)$",
    re.MULTILINE,
)

def markdown_preview_with_task_checkboxes(text: str) -> str:
    """Replace task list lines with ``indent + checkbox + body`` (no list marker).

    Unchecked (☐) and checked (☑) use the same body point size so they align visually.
    """

    def _repl(m: re.Match[str]) -> str:
        # Do not strip ``rest``: leading spaces after the checkbox are intentional indent.
        indent, inner, rest = m.group(1), m.group(2), (m.group(3) or "")
        checked = inner.strip().lower() == "x"
        mark = "\u2611" if checked else "\u2610"
        if rest.strip():
            sep = "" if rest.startswith((" ", "\t")) else " "
            return f"{indent}{mark}{sep}{rest}"
        return f"{indent}{mark}"

    return _TASK_ITEM_LINE.sub(_repl, text or "")


def _trim_edge_newlines(text: str, *, leading: bool = False, trailing: bool = False) -> str:
    out = text
    if leading:
        out = out.lstrip("\n\r")
    if trailing:
        out = out.rstrip("\n\r")
    return out


def _markdown_widget(value: str, style: ft.MarkdownStyleSheet) -> ft.Markdown:
    return ft.Markdown(
        value=value,
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
        soft_line_break=True,
        md_style_sheet=style,
    )


def _preview_scroll_row(content: ft.Control, avail_width_px: float) -> ft.Control:
    """One block row for the preview scroll column (flat sibling, not nested Column)."""
    return ft.Row(
        [
            ft.Container(
                width=avail_width_px,
                content=content,
            )
        ],
        alignment=ft.MainAxisAlignment.CENTER,
    )


def build_compose_preview_controls(text: str, avail_width_px: float) -> list[ft.Control]:
    """Return flat scroll-column rows (one per markdown/table block)."""
    src = markdown_preview_with_task_checkboxes(text)
    style = ui_theme.compose_preview_markdown_style_sheet()
    blocks = split_markdown_with_tables(src)

    # Fast path: no tables — single Markdown like the original preview.
    if len(blocks) == 1 and blocks[0].kind == "markdown":
        return [_preview_scroll_row(_markdown_widget(blocks[0].text, style), avail_width_px)]

    rows: list[ft.Control] = []
    prev_kind: str | None = None
    for i, block in enumerate(blocks):
        if block.kind == "markdown":
            md_text = block.text
            if not md_text.strip():
                continue
            if prev_kind == "table":
                md_text = _trim_edge_newlines(md_text, leading=True)
            if i + 1 < len(blocks) and blocks[i + 1].kind == "table":
                md_text = _trim_edge_newlines(md_text, trailing=True)
            block_ctrl: ft.Control = ft.Container(
                padding=ft.padding.only(
                    top=COMPOSE_PREVIEW_BLOCK_GAP_PX if prev_kind == "table" else 0,
                ),
                content=_markdown_widget(md_text, style),
            )
            rows.append(_preview_scroll_row(block_ctrl, avail_width_px))
            prev_kind = "markdown"
        else:
            if not block.rows:
                continue
            widths = compute_column_widths_px(block.rows, avail_width_px)
            block_ctrl = ft.Container(
                padding=ft.padding.only(
                    top=COMPOSE_PREVIEW_BLOCK_GAP_PX if prev_kind == "markdown" else 0,
                ),
                content=build_table_preview(
                    block.rows,
                    widths,
                    style,
                    has_header=block.has_header,
                ),
            )
            rows.append(_preview_scroll_row(block_ctrl, avail_width_px))
            prev_kind = "table"

    if not rows:
        return [_preview_scroll_row(_markdown_widget("", style), avail_width_px)]
    return rows
