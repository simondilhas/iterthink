"""Tests for KI sidebar LLM generation control."""

from __future__ import annotations

import asyncio

from iterthink.studio.llm_generation_control import (
    SIDEBAR_LLM_STOPPED_LABEL,
    MarkdownStudioLlmGenerationControl,
)


class _Stub(MarkdownStudioLlmGenerationControl):
    def __init__(self) -> None:
        self._init_sidebar_llm_control()
        self._chat_send_btn = None


def test_begin_end_lifecycle() -> None:
    stub = _Stub()
    assert not stub._sidebar_llm_generating
    gen = stub.begin_sidebar_llm()
    assert stub._sidebar_llm_generating
    assert gen == 1
    stub.end_sidebar_llm(gen)
    assert not stub._sidebar_llm_generating


def test_end_ignores_stale_generation() -> None:
    stub = _Stub()
    gen1 = stub.begin_sidebar_llm()
    stub.begin_sidebar_llm()
    stub.end_sidebar_llm(gen1)
    assert stub._sidebar_llm_generating


def test_request_stop_sets_cancel_flag() -> None:
    stub = _Stub()
    stub.begin_sidebar_llm()
    assert not stub.is_sidebar_llm_cancelled()
    stub.request_sidebar_llm_stop()
    assert stub.is_sidebar_llm_cancelled()


def test_sidebar_llm_display_text_stopped() -> None:
    stub = _Stub()
    stub.begin_sidebar_llm()
    stub.request_sidebar_llm_stop()
    assert stub.sidebar_llm_display_text("", empty_fallback="(Empty)") == SIDEBAR_LLM_STOPPED_LABEL
    assert stub.sidebar_llm_display_text("partial", empty_fallback="(Empty)") == "partial"


def test_sidebar_llm_display_text_normal() -> None:
    stub = _Stub()
    stub.begin_sidebar_llm()
    assert stub.sidebar_llm_display_text("hello", empty_fallback="(Empty)") == "hello"
    assert stub.sidebar_llm_display_text("", empty_fallback="(Empty)") == "(Empty)"


def test_stream_loop_respects_cancel() -> None:
    stub = _Stub()
    stub._sidebar_llm_cancel = asyncio.Event()
    stub._sidebar_llm_generating = True

    async def fake_stream():
        for piece in ("a", "b", "c"):
            if stub.is_sidebar_llm_cancelled():
                break
            yield piece

    async def run() -> str:
        acc = ""
        async for piece in fake_stream():
            acc += piece
            if piece == "b":
                stub.request_sidebar_llm_stop()
        return acc

    acc = asyncio.run(run())
    assert acc == "ab"
