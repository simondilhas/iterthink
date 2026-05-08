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


def result_card_bg() -> str:
    return ft.Colors.with_opacity(0.97, config.SURFACE_VARIANT)


def result_card_border() -> str:
    return ft.Colors.with_opacity(0.55, config.OUTLINE)


def page_color_scheme() -> ft.ColorScheme:
    """Full M3-style surfaces so filled TextFields / menus follow the active palette (esp. light)."""
    return ft.ColorScheme(
        primary=config.PRIMARY_COLOR,
        on_primary=config.ON_PRIMARY,
        surface=config.SURFACE_VARIANT,
        on_surface=config.ON_SURFACE,
        on_surface_variant=config.ON_SURFACE_VARIANT,
        outline=config.OUTLINE,
        surface_container=config.SURFACE,
        surface_container_lowest=config.PAGE_BG,
        surface_container_low=config.SURFACE_VARIANT,
        surface_container_high=config.SURFACE,
        surface_container_highest=config.SURFACE,
        surface_dim=config.SURFACE_VARIANT,
        surface_bright=config.SURFACE,
    )
