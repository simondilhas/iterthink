"""GFM table parsing, proportional column widths, and Flet preview widgets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import flet as ft

from .constants import COMPOSE_EDITOR_MONO_CHAR_WIDTH_EST_PX
from . import ui_theme


@dataclass
class MdBlock:
    kind: Literal["markdown", "table"]
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)
    has_header: bool = False


def compute_proportional_widths(
    natural: list[float],
    avail: float,
    *,
    min_width: float = 1.0,
) -> list[float]:
    """Scale ``natural`` widths to fill ``avail`` exactly, preserving content ratios."""
    if not natural:
        return []
    if avail <= 0:
        return [max(min_width, w) for w in natural]
    widths = [max(min_width, w) for w in natural]
    total = sum(widths)
    if total <= 0:
        return [avail / len(natural)] * len(natural)
    return [w * avail / total for w in widths]


def compute_column_widths_px(
    rows: list[list[str]],
    avail_px: float,
    *,
    char_w: float = COMPOSE_EDITOR_MONO_CHAR_WIDTH_EST_PX,
    min_col_px: float = 40.0,
    cell_pad_px: float = 8.0,
) -> list[float]:
    """Content-based column widths that always fill ``avail_px``."""
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    natural: list[float] = [min_col_px] * ncols
    for row in rows:
        for ci, cell in enumerate(row):
            if ci >= ncols:
                continue
            for line in (cell or "").split("\n"):
                w = len(line) * char_w + 2 * cell_pad_px
                natural[ci] = max(natural[ci], w)
    return compute_proportional_widths(natural, avail_px, min_width=min_col_px)


def _markdown_parser():
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"breaks": True})
    md.enable("table")
    return md


def _plaintext_from_inline(tokens: list[Any]) -> str:
    parts: list[str] = []

    def walk(tl: list[Any]) -> None:
        for t in tl:
            if t.type == "text":
                parts.append(t.content or "")
            elif t.type == "softbreak":
                parts.append(" ")
            elif t.type == "code_inline":
                parts.append(t.content or "")
            elif t.children:
                walk(t.children)

    walk(tokens)
    return "".join(parts)


def _parse_table_at(tokens: list[Any], start: int) -> tuple[list[list[str]], bool, int]:
    rows: list[list[str]] = []
    has_header = False
    i = start + 1
    in_thead = False
    n = len(tokens)
    while i < n and tokens[i].type != "table_close":
        t = tokens[i]
        if t.type == "thead_open":
            in_thead = True
            i += 1
            continue
        if t.type == "thead_close":
            in_thead = False
            i += 1
            continue
        if t.type in ("tbody_open", "tbody_close"):
            i += 1
            continue
        if t.type == "tr_open":
            if in_thead:
                has_header = True
            i += 1
            row: list[str] = []
            while i < n and tokens[i].type != "tr_close":
                if tokens[i].type in ("th_open", "td_open"):
                    i += 1
                    text = ""
                    if i < n and tokens[i].type == "inline":
                        text = _plaintext_from_inline(tokens[i].children or [])
                        i += 1
                    if i < n and tokens[i].type in ("th_close", "td_close"):
                        i += 1
                    row.append(text)
                else:
                    i += 1
            if i < n and tokens[i].type == "tr_close":
                i += 1
            rows.append(row)
            continue
        i += 1
    if i < n and tokens[i].type == "table_close":
        i += 1
    return rows, has_header, i


def split_markdown_with_tables(src: str) -> list[MdBlock]:
    """Split markdown into alternating text and GFM table blocks."""
    src = src or ""
    if not src.strip():
        return [MdBlock(kind="markdown", text=src)]

    md = _markdown_parser()
    tokens = md.parse(src)
    lines = src.splitlines(keepends=True)

    table_spans: list[tuple[int, int, int, int]] = []
    i = 0
    while i < len(tokens):
        if tokens[i].type == "table_open":
            start_i = i
            start_line = tokens[i].map[0] if tokens[i].map else 0
            j = i + 1
            while j < len(tokens) and tokens[j].type != "table_close":
                j += 1
            end_line = (
                tokens[j].map[1]
                if j < len(tokens) and tokens[j].map
                else tokens[i].map[1]
                if tokens[i].map
                else start_line + 1
            )
            table_spans.append((start_line, end_line, start_i, j))
            i = j + 1
        else:
            i += 1

    if not table_spans:
        return [MdBlock(kind="markdown", text=src)]

    blocks: list[MdBlock] = []
    line_cursor = 0
    for start_line, end_line, tok_start, tok_end in table_spans:
        if start_line > line_cursor:
            blocks.append(MdBlock(kind="markdown", text="".join(lines[line_cursor:start_line])))
        rows, has_header, _ = _parse_table_at(tokens, tok_start)
        blocks.append(MdBlock(kind="table", rows=rows, has_header=has_header))
        line_cursor = end_line

    if line_cursor < len(lines):
        blocks.append(MdBlock(kind="markdown", text="".join(lines[line_cursor:])))

    return blocks


def build_table_preview(
    rows: list[list[str]],
    widths_px: list[float],
    style_sheet: ft.MarkdownStyleSheet,
    *,
    has_header: bool = False,
) -> ft.Control:
    """Render a GFM table with explicit column widths."""
    if not rows:
        return ft.Container()

    head_style = style_sheet.table_head_text_style or style_sheet.p_text_style
    body_style = style_sheet.table_body_text_style or style_sheet.p_text_style
    cell_pad = style_sheet.table_cells_padding or ft.padding.symmetric(horizontal=8, vertical=4)
    row_border = ui_theme.compose_preview_table_row_border()
    cell_divider = ui_theme.compose_preview_table_cell_divider()
    table_pad = style_sheet.table_padding or ft.padding.only(bottom=8)

    ncols = len(widths_px)

    def _cell(text: str, width: float, *, header: bool, last_col: bool) -> ft.Control:
        return ft.Container(
            width=width,
            padding=cell_pad,
            border=None if last_col else cell_divider,
            alignment=ft.Alignment.TOP_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Text(
                text,
                style=head_style if header else body_style,
                selectable=True,
                no_wrap=False,
            ),
        )

    table_rows: list[ft.Control] = []
    table_w = sum(widths_px)
    for ri, row in enumerate(rows):
        is_header = has_header and ri == 0
        cells = [
            _cell(
                row[ci] if ci < len(row) else "",
                widths_px[ci],
                header=is_header,
                last_col=ci == ncols - 1,
            )
            for ci in range(ncols)
        ]
        table_rows.append(
            ft.Container(
                width=table_w,
                border=row_border,
                content=ft.Row(
                    cells,
                    spacing=0,
                    wrap=False,
                    vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
            )
        )

    return ft.Container(
        width=table_w,
        padding=table_pad,
        content=ft.Column(table_rows, spacing=0, tight=True),
    )
