"""Flet page bootstrap."""

import sys

import flet as ft
from flet.controls.types import PagePlatform

from iterthink import config
from iterthink.db import bootstrap
from iterthink.ollama_util import ollama_error_message
from iterthink.studio import MarkdownStudio


async def main(page: ft.Page) -> None:
    page.title = "Iterthink — Markdown"
    if page.web:
        await page.browser_context_menu.disable()
    if not page.web:
        sym = config.APP_SYMBOL_PNG
        if sym.is_file():
            page.window.icon = str(sym.resolve())
    page.theme_mode = ft.ThemeMode.DARK

    pl = getattr(page, "platform", None)
    use_native_csd = pl in (PagePlatform.LINUX, PagePlatform.WINDOWS)
    if pl is None and not page.web:
        use_native_csd = sys.platform.startswith("linux") or sys.platform == "win32"

    # Flush chrome with window edges on native CSD; body insets are applied inside the studio layout.
    if use_native_csd:
        page.padding = ft.padding.only(left=0, right=0, bottom=12, top=0)
    else:
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

    bootstrap.bootstrap_database()

    studio = MarkdownStudio(page)
    page.add(studio.build())
    await studio._startup_open_default_note()
    studio._refresh_title_bar()

    if use_native_csd:
        page.window.title_bar_hidden = True
        page.update()

    async def _save_on_boundary() -> None:
        if studio.current_path and studio._is_dirty():
            await studio.save_file(silent=True, snapshot_reason="pre_switch")

    def on_window_event(e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.RESIZED:
            studio.reflow_columns()
        elif e.type in (ft.WindowEventType.BLUR, ft.WindowEventType.CLOSE):
            page.run_task(_save_on_boundary)

    page.window.on_event = on_window_event

    async def _ollama_startup_check() -> None:
        if getattr(studio, "ki_tier", "local") != "local":
            return
        try:
            await studio.ollama.list()
        except BaseException as ex:
            studio._snack(
                f"Ollama not reachable ({studio.ollama_model}): {ollama_error_message(ex)}. "
                "Start `ollama serve` or set OLLAMA_HOST."
            )

    await _ollama_startup_check()
    page.run_task(studio._refresh_ki_chat_model_dropdown)
