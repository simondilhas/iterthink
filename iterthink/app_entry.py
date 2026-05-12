"""Flet page bootstrap."""

import asyncio
import sys

import flet as ft
from flet.controls.types import PagePlatform

from iterthink import config
from iterthink.studio import ui_theme
from iterthink.db import bootstrap
from iterthink.db.session import reset_engine_cache
from iterthink.ai.local_embedding import prepare_runtime_embedding_model_sync
from iterthink.ai.ollama_util import ollama_error_message
from iterthink.studio import MarkdownStudio


async def main(page: ft.Page) -> None:
    page.title = "Iterthink — Markdown"
    if page.web:
        await page.browser_context_menu.disable()
    if not page.web:
        sym = config.APP_SYMBOL_PNG if config.APP_SYMBOL_PNG.is_file() else config.APP_SYMBOL_SVG
        if sym.is_file():
            page.window.icon = str(sym.resolve())
    page.theme_mode = ft.ThemeMode.LIGHT if config.IS_LIGHT else ft.ThemeMode.DARK

    pl = getattr(page, "platform", None)
    use_native_csd = pl in (PagePlatform.LINUX, PagePlatform.WINDOWS)
    if pl is None and not page.web:
        use_native_csd = sys.platform.startswith("linux") or sys.platform == "win32"

    # Flush chrome with window edges on native CSD; body insets are applied inside the studio layout.
    if use_native_csd:
        page.padding = ft.padding.only(left=0, right=0, bottom=12, top=0)
    else:
        page.padding = 12

    page.bgcolor = config.PAGE_BG
    page.theme = ft.Theme(color_scheme=ui_theme.page_color_scheme())

    # Match MarkdownStudio: same store path + fresh engine after YAML paths load.
    config.refresh()
    reset_engine_cache()
    bootstrap.bootstrap_database()

    studio = MarkdownStudio(page)
    page.add(studio.build())
    await studio._startup_open_default_note()
    studio._refresh_title_bar()

    if use_native_csd:
        page.window.title_bar_hidden = True
        page.update()

    async def _save_on_boundary() -> None:
        studio._flush_review_edits_if_changed()
        if studio.current_path and studio._is_dirty():
            await studio.save_file(silent=True, snapshot_reason="pre_switch")

    async def _save_on_window_close() -> None:
        """Disk + DB only; skip Flet UI refresh so native teardown does not race channel updates."""
        studio._flush_review_edits_if_changed(refresh_compare_ui=False)
        if studio.current_path and studio._is_dirty():
            await studio.save_file(
                silent=True,
                snapshot_reason="pre_switch",
                for_shutdown=True,
            )

    def on_window_event(e: ft.WindowEvent) -> None:
        if e.type == ft.WindowEventType.RESIZED:
            studio.reflow_columns()
        elif e.type == ft.WindowEventType.FOCUS:
            if not page.web:
                page.run_task(studio._check_file_drift_async)
        elif e.type == ft.WindowEventType.CLOSE:
            page.run_task(_save_on_window_close)
        elif e.type == ft.WindowEventType.BLUR:
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

    async def _embedding_model_warmup() -> None:
        if page.web:
            return
        try:
            await asyncio.to_thread(prepare_runtime_embedding_model_sync)
        except BaseException:
            studio._snack(
                "Could not download the paragraph-compare embedding model from Hugging Face. "
                "Check your network once; for restricted networks set HF_TOKEN. "
                "Compare-tab embeddings need this download the first time."
            )

    page.run_task(_embedding_model_warmup)
    page.run_task(studio._refresh_ki_chat_model_dropdown)
    if not page.web:
        page.run_task(studio._periodic_file_drift_loop)
