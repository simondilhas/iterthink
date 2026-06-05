"""Tests for CSD header auto-hide collapse/expand state."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft

from iterthink.studio.shell import MarkdownStudioShell


class _HeaderStub(MarkdownStudioShell):
    def __init__(self) -> None:
        self._header_shell: ft.Container | None = ft.Container(height=50, opacity=1.0)
        self._header_menu_open = 2
        self._header_hide_gen = 0
        self.page = SimpleNamespace(run_task=MagicMock())


def test_collapse_header_bar_passes_through_and_resets_menu_open() -> None:
    stub = _HeaderStub()
    sh = stub._header_shell
    assert sh is not None

    stub._collapse_header_bar()

    assert sh.height == 0
    assert sh.opacity == 0.0
    assert sh.ignore_interactions is True
    assert sh.clip_behavior == ft.ClipBehavior.HARD_EDGE
    assert stub._header_menu_open == 0


def test_expand_header_bar_enables_interactions_and_unclips() -> None:
    stub = _HeaderStub()
    sh = stub._header_shell
    assert sh is not None
    sh.height = 0
    sh.opacity = 0.0
    sh.ignore_interactions = True
    sh.clip_behavior = ft.ClipBehavior.HARD_EDGE

    stub._expand_header_bar()

    assert sh.height == 50
    assert sh.opacity == 1.0
    assert sh.ignore_interactions is False
    assert sh.clip_behavior == ft.ClipBehavior.NONE
    assert stub._header_hide_gen == 1
