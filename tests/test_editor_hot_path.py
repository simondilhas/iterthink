"""Tests for editor keystroke hot path: shortcuts and debounce gens."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from iterthink.studio.content_tree import MarkdownStudioContentTree
from iterthink.studio.ki_sidebar import MarkdownStudioKiSidebar


def _key_event(*, key: str, ctrl: bool = False, meta: bool = False) -> SimpleNamespace:
    return SimpleNamespace(key=key, ctrl=ctrl, meta=meta)


class _KeyboardStub(MarkdownStudioKiSidebar):
    """Minimal object to exercise _on_page_keyboard without a Flet page tree."""

    def __init__(self) -> None:
        self._compose_tab_inline_rename_active = False
        self._run_tasks: list[tuple] = []
        self.page = SimpleNamespace(run_task=lambda fn, *args: self._run_tasks.append((fn, args)))
        self.save_file = AsyncMock()
        self.toggle_right = MagicMock()


def test_ctrl_s_queues_save() -> None:
    stub = _KeyboardStub()
    stub._on_page_keyboard(_key_event(key="s", ctrl=True))
    assert len(stub._run_tasks) == 1
    fn, args = stub._run_tasks[0]
    assert fn is stub.save_file
    assert args == (None,)


def test_meta_s_queues_save() -> None:
    stub = _KeyboardStub()
    stub._on_page_keyboard(_key_event(key="S", meta=True))
    assert len(stub._run_tasks) == 1
    assert stub._run_tasks[0][0] is stub.save_file


class _ContentTreeDebounceStub(MarkdownStudioContentTree):
    def __init__(self) -> None:
        self._left_sidebar_tab = 1
        self._content_tree_gen = 0
        self._run_tasks: list[tuple] = []
        self.page = SimpleNamespace(
            run_task=lambda fn, *args: self._run_tasks.append((fn, args))
        )
        self._rebuild_calls = 0

    def _rebuild_content_tree(self) -> None:
        self._rebuild_calls += 1


def test_kick_debounced_content_tree_increments_gen() -> None:
    stub = _ContentTreeDebounceStub()
    stub._kick_debounced_content_tree()
    assert stub._content_tree_gen == 1
    assert len(stub._run_tasks) == 1
    stub._kick_debounced_content_tree()
    assert stub._content_tree_gen == 2
    assert len(stub._run_tasks) == 2


def test_debounced_rebuild_content_tree_stale_gen_noops() -> None:
    stub = _ContentTreeDebounceStub()
    stub._kick_debounced_content_tree()
    gen = stub._content_tree_gen
    stub._content_tree_gen += 1

    async def _run() -> None:
        await stub._debounced_rebuild_content_tree(gen)

    asyncio.run(_run())
    assert stub._rebuild_calls == 0


def test_debounced_rebuild_content_tree_runs_when_current() -> None:
    stub = _ContentTreeDebounceStub()
    stub._kick_debounced_content_tree()
    gen = stub._content_tree_gen

    async def _run() -> None:
        await stub._debounced_rebuild_content_tree(gen)

    asyncio.run(_run())
    assert stub._rebuild_calls == 1
