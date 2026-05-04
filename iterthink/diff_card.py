"""Unified diff rendering (inline word-level) for margin annotator."""

from __future__ import annotations

import difflib
import re
from typing import Any, Literal

import flet as ft

from iterthink import config
from iterthink.ollama_util import chat_response_text

SemanticKind = Literal["STABLE", "NEW"]

# Low-opacity highlights (ARGB ~25%)
_BG_NEW = "#402ECC71"
_BG_DEL = "#40FF5252"


def _tokenize(s: str) -> list[str]:
    if not s:
        return []
    return re.findall(r"\s+|\S+", s)


def build_unified_spans(
    old_text: str,
    new_text: str,
    *,
    base_size: int = 12,
    base_color: str = ft.Colors.GREY_400,
) -> list[ft.TextSpan]:
    """Word-level inline diff for margin: deletions (red + strikethrough), insertions (green)."""
    base = ft.TextStyle(size=base_size, color=base_color)
    a = _tokenize(old_text)
    b = _tokenize(new_text)
    if not a and not b:
        return [ft.TextSpan(text=" ", style=base)]

    spans: list[ft.TextSpan] = []
    sm = difflib.SequenceMatcher(None, a, b)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            chunk = "".join(a[i1:i2])
            if chunk:
                spans.append(ft.TextSpan(text=chunk, style=base))
        elif tag == "delete":
            chunk = "".join(a[i1:i2])
            if chunk:
                spans.append(
                    ft.TextSpan(
                        text=chunk,
                        style=ft.TextStyle(
                            size=base_size,
                            color=base_color,
                            bgcolor=_BG_DEL,
                            decoration=ft.TextDecoration.LINE_THROUGH,
                            decoration_color=ft.Colors.RED_200,
                        ),
                    )
                )
        elif tag == "insert":
            chunk = "".join(b[j1:j2])
            if chunk:
                spans.append(
                    ft.TextSpan(
                        text=chunk,
                        style=ft.TextStyle(size=base_size, color=ft.Colors.GREY_200, bgcolor=_BG_NEW),
                    )
                )
        else:  # replace
            for t in a[i1:i2]:
                spans.append(
                    ft.TextSpan(
                        text=t,
                        style=ft.TextStyle(
                            size=base_size,
                            color=base_color,
                            bgcolor=_BG_DEL,
                            decoration=ft.TextDecoration.LINE_THROUGH,
                            decoration_color=ft.Colors.RED_200,
                        ),
                    )
                )
            for t in b[j1:j2]:
                spans.append(
                    ft.TextSpan(
                        text=t,
                        style=ft.TextStyle(size=base_size, color=ft.Colors.GREY_200, bgcolor=_BG_NEW),
                    )
                )

    if not spans:
        return [ft.TextSpan(text=" ", style=base)]
    return spans


def _clip(s: str, max_len: int = 2800) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


async def judge_semantic(ollama: Any, model: str, original: str, revised: str) -> SemanticKind:
    """Ask Ollama whether the revision shifts intent (NEW) or not (STABLE)."""
    messages = [
        {
            "role": "system",
            "content": "Reply with exactly one word: STABLE or NEW. No punctuation.",
        },
        {
            "role": "user",
            "content": (
                "Compare A vs B. STABLE = same core meaning and intent. "
                "NEW = main message, recommendation, or stance changed.\n\n"
                f"A:\n{_clip(original)}\n\nB:\n{_clip(revised)}"
            ),
        },
    ]
    try:
        resp = await ollama.chat(model=model, messages=messages, stream=False)
        raw = (chat_response_text(resp) or "").strip().upper()
        for token in raw.replace(",", " ").split():
            if token in ("NEW", "STABLE"):
                return "NEW" if token == "NEW" else "STABLE"
    except BaseException:
        pass
    return "STABLE"
