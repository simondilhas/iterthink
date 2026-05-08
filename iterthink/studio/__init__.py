"""Flet UI package: ``MarkdownStudio`` is lazy-loaded; ``ui_theme`` loads without the app."""

from __future__ import annotations

import importlib
from typing import Any

# Eager submodule so ``from . import ui_theme`` in sibling modules does not hit ``__getattr__``.
ui_theme = importlib.import_module("iterthink.studio.ui_theme")

__all__ = ("MarkdownStudio", "ui_theme")


def __getattr__(name: str) -> Any:
    if name == "MarkdownStudio":
        from .markdown_studio import MarkdownStudio

        return MarkdownStudio
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *(__all__)})
