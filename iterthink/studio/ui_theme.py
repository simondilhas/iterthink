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


def compose_preview_markdown_style_sheet() -> ft.MarkdownStyleSheet:
    """Focus preview / help: monospace like the editor; strong = body + weight only."""
    from .constants import COMPARE_COL_FONT_SIZE, COMPARE_COL_LINE_HEIGHT

    fs = int(float(COMPARE_COL_FONT_SIZE))
    lh = float(COMPARE_COL_LINE_HEIGHT)
    ec = editor_text_color()
    ff = "monospace"
    base = ft.TextStyle(size=fs, height=lh, color=ec, font_family=ff)
    strong = base.copy(weight=ft.FontWeight.W_700)
    em = base.copy(italic=True)
    return ft.MarkdownStyleSheet(
        p_text_style=base,
        strong_text_style=strong,
        em_text_style=em,
        # Nested lists read flat when indent is tight; match monospace column feel.
        list_indent=28,
        blockquote_padding=ft.padding.only(left=10, top=2, bottom=2, right=4),
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
