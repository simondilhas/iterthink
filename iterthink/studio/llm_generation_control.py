"""KI sidebar LLM generation: shared cancel flag and send/stop button state."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from iterthink import config

from .util import ctrl_on_page as _ctrl_on_page

SIDEBAR_LLM_STOPPED_LABEL = "(Stopped)"


class MarkdownStudioLlmGenerationControl:
    """Mixin: track in-flight sidebar LLM work and expose send → stop on the chat button."""

    def _init_sidebar_llm_control(self) -> None:
        self._sidebar_llm_generating = False
        self._sidebar_llm_gen = 0
        self._sidebar_llm_cancel: asyncio.Event | None = None
        self._sidebar_llm_task: asyncio.Task[Any] | None = None

    def begin_sidebar_llm(self) -> int:
        """Mark sidebar LLM active; cancel any prior generation."""
        if self._sidebar_llm_generating:
            self.request_sidebar_llm_stop()
        self._sidebar_llm_gen += 1
        gen = self._sidebar_llm_gen
        self._sidebar_llm_cancel = asyncio.Event()
        self._sidebar_llm_generating = True
        try:
            self._sidebar_llm_task = asyncio.current_task()
        except RuntimeError:
            self._sidebar_llm_task = None
        self.sync_chat_send_button()
        return gen

    def end_sidebar_llm(self, gen: int) -> None:
        """Clear active state when the generation session ends (ignore stale sessions)."""
        if gen != self._sidebar_llm_gen:
            return
        self._sidebar_llm_generating = False
        self._sidebar_llm_cancel = None
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if self._sidebar_llm_task is current:
            self._sidebar_llm_task = None
        self.sync_chat_send_button()

    def request_sidebar_llm_stop(self) -> None:
        """User pressed Stop — signal stream loops and cancel the owning task."""
        ev = self._sidebar_llm_cancel
        if ev is not None:
            ev.set()
        task = self._sidebar_llm_task
        if task is not None and not task.done():
            task.cancel()

    def is_sidebar_llm_cancelled(self) -> bool:
        ev = self._sidebar_llm_cancel
        return ev is not None and ev.is_set()

    def sidebar_llm_display_text(self, acc: str, *, empty_fallback: str) -> str:
        """Format assistant text after stream/non-stream, honoring stop."""
        text = (acc or "").strip()
        if self.is_sidebar_llm_cancelled():
            if text:
                return text
            return SIDEBAR_LLM_STOPPED_LABEL
        return text or empty_fallback

    def sync_chat_send_button(self) -> None:
        btn = getattr(self, "_chat_send_btn", None)
        if btn is None:
            return
        if self._sidebar_llm_generating:
            btn.icon = ft.Icons.STOP
            btn.tooltip = "Stop"
        else:
            btn.icon = ft.Icons.SEND
            btn.tooltip = "Send"
        if _ctrl_on_page(btn):
            btn.update()

    async def _on_chat_send_click(self, _e: ft.ControlEvent | None = None) -> None:
        if self._sidebar_llm_generating:
            self.request_sidebar_llm_stop()
            return
        await self._send_chat_message(_e)
