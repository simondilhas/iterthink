"""Flet page bootstrap."""

import sys

import flet as ft
from flet.controls.types import PagePlatform

from iterthink import config
from iterthink.ollama_util import ollama_error_message
from iterthink.studio import MarkdownStudio


async def main(page: ft.Page) -> None:
    page.title = "Iterthink — Markdown"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 12
    page.bgcolor = "#121212"
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=config.FEDORA_BLUE,
            on_primary=ft.Colors.WHITE,
            surface=config.SURFACE_VARIANT,
            on_surface=ft.Colors.GREY_100,
            surface_container=config.SURFACE,
        ),
    )

    studio = MarkdownStudio(page)
    page.add(studio.build())
    studio._refresh_title_bar()

    pl = getattr(page, "platform", None)
    use_native_csd = pl in (PagePlatform.LINUX, PagePlatform.WINDOWS)
    if pl is None and not page.web:
        use_native_csd = sys.platform.startswith("linux") or sys.platform == "win32"
    if use_native_csd:
        page.window.title_bar_hidden = True
        page.update()

    def on_window_event(e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.RESIZED:
            studio.reflow_columns()

    page.window.on_event = on_window_event

    async def _ollama_startup_check() -> None:
        try:
            await studio.ollama.list()
        except BaseException as ex:
            studio._snack(
                f"Ollama not reachable ({studio.ollama_model}): {ollama_error_message(ex)}. "
                "Start `ollama serve` or set OLLAMA_HOST."
            )

    await _ollama_startup_check()
