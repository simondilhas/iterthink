"""KI / margin chat bubbles: input, redacted input, output to accept."""

from __future__ import annotations

import flet as ft

from iterthink import config
from iterthink.studio.util import ctrl_on_page as _ctrl_on_page


def privacy_labeled_quote(label: str, text: str) -> ft.Control:
    header = ft.Text(
        label,
        size=12,
        weight=ft.FontWeight.W_600,
        color=config.ON_SURFACE,
    )
    body = ft.Text(
        text,
        size=12,
        selectable=True,
        italic=True,
        color=config.ON_SURFACE_VARIANT,
    )
    quote_box = ft.Container(
        content=body,
        padding=ft.padding.only(left=8, top=2, bottom=2),
        border=ft.border.only(left=ft.BorderSide(2, config.PRIMARY_COLOR)),
    )
    return ft.Column([header, quote_box], tight=True, spacing=4)


def build_privacy_turn_bubble(
    *,
    input_text: str,
    redacted_text: str,
    output_text: str,
    footer: ft.Control | None = None,
    align: ft.Alignment = ft.Alignment.CENTER_LEFT,
    bgcolor: str | None = None,
) -> ft.Container:
    """Three labeled blocks: input → redacted input → output (+ optional footer)."""
    parts: list[ft.Control] = [
        privacy_labeled_quote("Input", input_text),
        privacy_labeled_quote("Redacted input", redacted_text),
        privacy_labeled_quote("Output to accept", output_text),
    ]
    if footer is not None:
        parts.append(footer)
    bg = bgcolor or ft.Colors.with_opacity(0.14, config.OUTLINE)
    return ft.Container(
        content=ft.Column(parts, tight=True, spacing=8),
        padding=ft.padding.symmetric(horizontal=10, vertical=8),
        bgcolor=bg,
        border_radius=10,
        alignment=align,
    )


def append_privacy_turn_to_history(
    history: ft.ListView,
    bubble: ft.Container,
) -> None:
    history.controls.append(bubble)
    if _ctrl_on_page(history):
        history.update()
