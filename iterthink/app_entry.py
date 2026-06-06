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
from iterthink.ai.privacy_shield_gguf import is_gguf_ready
from iterthink.ai.privacy_shield_llm import require_llama_cpp_import
from iterthink.ai.privacy_shield_model import ensure_privacy_shield_model_sync
from iterthink.studio.privacy_shield_ui import begin_privacy_shield_download
from iterthink.studio.prompts_merge_ui import show_prompt_merge_dialog
from iterthink.studio import MarkdownStudio
from iterthink import prompts


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
    prompt_sync = prompts.sync_with_defaults()

    studio = MarkdownStudio(page)
    page.add(studio.build())
    await studio._startup_open_default_note()
    studio._refresh_title_bar()

    if prompt_sync.added_ids:
        n = len(prompt_sync.added_ids)
        studio._snack(f"Added {n} prompt{'s' if n != 1 else ''} from defaults.")
    if prompt_sync.pending:
        await show_prompt_merge_dialog(page, studio, prompt_sync.pending)

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

    async def _ocr_model_warmup() -> None:
        if page.web or not config.OCR_ENABLED:
            return
        if config.OCR_ENGINE == "ollama":
            from iterthink.ai.ollama_ocr import check_ollama_ocr_ready

            ok, reason = await check_ollama_ocr_ready(studio.ollama, config.OCR_MODEL)
            if not ok:
                studio._snack(
                    f"OCR uses Ollama vision but is not ready: {reason}. "
                    "Start `ollama serve` or pull the configured model."
                )
            return
        try:
            from iterthink.ai.local_ocr import prepare_runtime_ocr_model_sync

            await asyncio.to_thread(prepare_runtime_ocr_model_sync)
        except BaseException:
            studio._snack(
                "Could not download the OCR model (RapidOCR). "
                "Check your network once; for restricted networks set HF_TOKEN."
            )

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

    async def _reranker_model_warmup() -> None:
        if page.web or not config.RAG_RERANKER_ENABLED:
            return
        try:
            from iterthink.ai.local_reranker import prepare_runtime_reranker_sync

            await asyncio.to_thread(prepare_runtime_reranker_sync)
        except BaseException:
            pass

    async def _rag_index_startup() -> None:
        await _embedding_model_warmup()
        await _reranker_model_warmup()
        await studio._rag_startup_index_async()

    async def _privacy_shield_model_warmup() -> None:
        if page.web or not config.PRIVACY_SHIELD_ENABLED:
            return
        if not is_gguf_ready():
            handle = await begin_privacy_shield_download(studio)
            loop = asyncio.get_running_loop()

            def on_progress(frac: float) -> None:
                asyncio.run_coroutine_threadsafe(
                    handle.set_progress(frac, "Downloading privacy shield model from Hugging Face…"),
                    loop,
                )

            try:
                await asyncio.to_thread(ensure_privacy_shield_model_sync, on_progress)
            except BaseException:
                studio._snack(
                    f"Could not download the privacy-shield model ({config.PRIVACY_SHIELD_CACHE_NAME}). "
                    "Check your network; for restricted networks set HF_TOKEN."
                )
                return
            finally:
                await handle.close()
        try:
            await asyncio.to_thread(require_llama_cpp_import)
        except BaseException as ex:
            studio._snack(str(ex))

    page.run_task(_ocr_model_warmup)
    page.run_task(studio._hydrate_rag_status_on_startup)
    page.run_task(_rag_index_startup)
    page.run_task(_privacy_shield_model_warmup)
    page.run_task(studio._refresh_ki_chat_model_dropdown)
    if not page.web:
        page.run_task(studio._periodic_file_drift_loop)
