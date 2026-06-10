"""Theme-aware Flet styles (depends on iterthink.config after refresh)."""

from __future__ import annotations

import flet as ft

from iterthink import config


def compare_candidate_dropdown_option_style() -> ft.ButtonStyle:
    """Compare-tab version dropdown rows: contrast in both appearances."""
    if config.IS_LIGHT:
        sel_bg = config.SURFACE_VARIANT
        sel_fg = config.ON_SURFACE
    else:
        sel_bg = ft.Colors.with_opacity(0.38, config.ON_SURFACE)
        sel_fg = config.SURFACE
    return ft.ButtonStyle(
        color={
            ft.ControlState.DEFAULT: config.ON_SURFACE_VARIANT,
            ft.ControlState.HOVERED: config.ON_SURFACE,
            ft.ControlState.SELECTED: sel_fg,
        },
        bgcolor={
            ft.ControlState.DEFAULT: config.SURFACE,
            ft.ControlState.SELECTED: sel_bg,
        },
        overlay_color=ft.Colors.with_opacity(0.10, config.ON_SURFACE),
    )


def compare_slot_pill_colors(kind: str) -> tuple[str, str]:
    """History/Future compare column: (bgcolor, fgcolor) from config tokens + appearance."""
    fg = config.ON_SURFACE
    ph = config.PRIMARY_COLOR
    hi = config.HIGHLIGHT
    hl_is_primary = ph.strip().lower() == hi.strip().lower()

    def wash(color: str, alpha_light: float, alpha_dark: float) -> str:
        a = alpha_light if config.IS_LIGHT else alpha_dark
        return ft.Colors.with_opacity(a, color)

    if kind == "stable":
        return wash(config.OUTLINE, 0.18, 0.18), config.ON_SURFACE_VARIANT
    if kind == "added":
        return wash(config.SUCCESS, 0.45, 0.38), fg
    if kind == "removed":
        red = "#EF5350"
        return wash(red, 0.32, 0.28), fg
    if kind == "refined":
        return wash(ph, 0.26, 0.30), fg
    if kind == "rephrased":
        accent = hi if not hl_is_primary else config.ON_SURFACE_SOFT
        return wash(accent, 0.28, 0.32), fg
    if kind == "modified":
        return wash(config.OUTLINE, 0.35, 0.42), fg
    return wash(config.OUTLINE, 0.22, 0.22), config.ON_SURFACE_VARIANT


def compare_moved_pill_colors() -> tuple[str, str]:
    """Neutral 'Moved' badge on compare rows (displacement), theme-aware."""
    if config.IS_LIGHT:
        return ft.Colors.with_opacity(0.32, config.OUTLINE), config.ON_SURFACE
    return ft.Colors.with_opacity(0.28, config.PRIMARY_COLOR), config.ON_SURFACE


def outline_muted(*, alpha: float | None = None) -> str:
    a = alpha if alpha is not None else (0.32 if config.IS_LIGHT else 0.42)
    return ft.Colors.with_opacity(a, config.OUTLINE)


def on_surface_soft_ui() -> str:
    """Body text softer than on_surface (e.g. dark mode blue at 90%)."""
    if config.IS_LIGHT:
        return ft.Colors.with_opacity(0.88, config.ON_SURFACE_SOFT)
    return ft.Colors.with_opacity(0.9, config.ON_SURFACE_SOFT)


def editor_text_color() -> str:
    """Main monospace editor foreground."""
    if config.IS_LIGHT:
        return config.ON_SURFACE
    return on_surface_soft_ui()


def compose_preview_table_row_border() -> ft.Border:
    border_c = ft.Colors.with_opacity(0.35, config.OUTLINE)
    return ft.border.only(bottom=ft.BorderSide(1, border_c))


def compose_preview_table_cell_divider() -> ft.Border:
    border_c = ft.Colors.with_opacity(0.35, config.OUTLINE)
    return ft.border.only(right=ft.BorderSide(1, border_c))


def compose_preview_table_cell_border() -> ft.Border:
    """Legacy combined border for MarkdownStyleSheet (non-hybrid tables)."""
    border_c = ft.Colors.with_opacity(0.35, config.OUTLINE)
    return ft.border.only(
        bottom=ft.BorderSide(1, border_c),
        right=ft.BorderSide(1, border_c),
    )


def compose_preview_horizontal_rule_decoration() -> ft.BoxDecoration:
    """Focus preview `---`: single hairline, muted to match table borders."""
    line_c = outline_muted(alpha=0.22 if config.IS_LIGHT else 0.28)
    return ft.BoxDecoration(
        border=ft.border.only(top=ft.BorderSide(1, line_c)),
    )


def compose_preview_markdown_style_sheet() -> ft.MarkdownStyleSheet:
    """Focus preview / help: serif reading face; strong = body + weight only."""
    from .constants import (
        COMPARE_COL_FONT_SIZE,
        COMPARE_COL_LINE_HEIGHT,
        COMPOSE_PREVIEW_BLOCK_GAP_PX,
        COMPOSE_PREVIEW_PARAGRAPH_BOTTOM_PAD_PX,
    )

    fs = int(float(COMPARE_COL_FONT_SIZE))
    lh = float(COMPARE_COL_LINE_HEIGHT)
    ec = editor_text_color()
    ff = "serif"
    base = ft.TextStyle(size=fs, height=lh, color=ec, font_family=ff)
    strong = base.copy(weight=ft.FontWeight.W_700)
    em = base.copy(italic=True)

    def _heading(*, scale: float, top: int, bottom: int) -> tuple[ft.TextStyle, ft.Padding]:
        style = ft.TextStyle(
            size=max(fs + 1, int(round(fs * scale))),
            height=lh,
            color=ec,
            font_family=ff,
            weight=ft.FontWeight.W_700,
        )
        pad = ft.padding.only(top=top, bottom=bottom)
        return style, pad

    h1_style, h1_pad = _heading(scale=1.55, top=12, bottom=0)
    h2_style, h2_pad = _heading(scale=1.35, top=10, bottom=0)
    h3_style, h3_pad = _heading(scale=1.2, top=8, bottom=0)
    h4_style, h4_pad = _heading(scale=1.1, top=6, bottom=0)
    h5_style, h5_pad = _heading(scale=1.0, top=4, bottom=0)
    h6_style, h6_pad = _heading(scale=0.95, top=4, bottom=0)

    return ft.MarkdownStyleSheet(
        block_spacing=COMPOSE_PREVIEW_BLOCK_GAP_PX,
        p_text_style=base,
        p_padding=ft.padding.only(bottom=COMPOSE_PREVIEW_PARAGRAPH_BOTTOM_PAD_PX),
        h1_text_style=h1_style,
        h1_padding=h1_pad,
        h2_text_style=h2_style,
        h2_padding=h2_pad,
        h3_text_style=h3_style,
        h3_padding=h3_pad,
        h4_text_style=h4_style,
        h4_padding=h4_pad,
        h5_text_style=h5_style,
        h5_padding=h5_pad,
        h6_text_style=h6_style,
        h6_padding=h6_pad,
        strong_text_style=strong,
        em_text_style=em,
        list_indent=28,
        blockquote_padding=ft.padding.only(left=10, top=2, bottom=2, right=4),
        table_head_text_style=strong,
        table_body_text_style=base,
        table_cells_padding=ft.padding.symmetric(horizontal=8, vertical=4),
        table_padding=ft.padding.only(bottom=8),
        table_cells_decoration=ft.BoxDecoration(border=compose_preview_table_cell_border()),
        horizontal_rule_decoration=compose_preview_horizontal_rule_decoration(),
    )


def compose_wysiwyg_block_markdown_style_sheet() -> ft.MarkdownStyleSheet:
    """Per-block wysiwyg read surface: inter-block gaps live on row margins, not Markdown."""
    sheet = compose_preview_markdown_style_sheet()
    return ft.MarkdownStyleSheet(
        block_spacing=0,
        p_text_style=sheet.p_text_style,
        p_padding=ft.padding.all(0),
        h1_text_style=sheet.h1_text_style,
        h1_padding=sheet.h1_padding,
        h2_text_style=sheet.h2_text_style,
        h2_padding=sheet.h2_padding,
        h3_text_style=sheet.h3_text_style,
        h3_padding=sheet.h3_padding,
        h4_text_style=sheet.h4_text_style,
        h4_padding=sheet.h4_padding,
        h5_text_style=sheet.h5_text_style,
        h5_padding=sheet.h5_padding,
        h6_text_style=sheet.h6_text_style,
        h6_padding=sheet.h6_padding,
        strong_text_style=sheet.strong_text_style,
        em_text_style=sheet.em_text_style,
        list_indent=sheet.list_indent,
        list_bullet_padding=ft.padding.symmetric(vertical=2),
        blockquote_padding=sheet.blockquote_padding,
        table_head_text_style=sheet.table_head_text_style,
        table_body_text_style=sheet.table_body_text_style,
        table_cells_padding=sheet.table_cells_padding,
        table_padding=sheet.table_padding,
        table_cells_decoration=sheet.table_cells_decoration,
        horizontal_rule_decoration=sheet.horizontal_rule_decoration,
    )


def result_card_bg() -> str:
    return ft.Colors.with_opacity(0.97, config.SURFACE_VARIANT)


def result_card_border() -> str:
    return ft.Colors.with_opacity(0.55, config.OUTLINE)


def soft_elevation_shadow() -> ft.BoxShadow:
    """Card / overlay shadow: black in dark mode, outline-tinted in light (avoids grey smudge)."""
    c = (
        ft.Colors.with_opacity(0.22, config.OUTLINE)
        if config.IS_LIGHT
        else ft.Colors.with_opacity(0.45, ft.Colors.BLACK)
    )
    return ft.BoxShadow(
        blur_radius=18,
        spread_radius=0,
        color=c,
        offset=ft.Offset(0, 6),
    )


def page_color_scheme() -> ft.ColorScheme:
    """Full M3-style surfaces so filled TextFields / menus follow the active palette (esp. light)."""
    return ft.ColorScheme(
        primary=config.PRIMARY_COLOR,
        on_primary=config.ON_PRIMARY,
        surface=config.SURFACE_VARIANT,
        on_surface=config.ON_SURFACE,
        on_surface_variant=config.ON_SURFACE_VARIANT,
        outline=config.OUTLINE,
        surface_tint=ft.Colors.TRANSPARENT,
        surface_container=config.SURFACE,
        surface_container_lowest=config.PAGE_BG,
        surface_container_low=config.SURFACE_VARIANT,
        surface_container_high=config.SURFACE,
        surface_container_highest=config.SURFACE,
        surface_dim=config.SURFACE_VARIANT,
        surface_bright=config.SURFACE,
    )
