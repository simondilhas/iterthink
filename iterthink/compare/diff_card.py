"""Unified diff rendering (inline word-level) for margin annotator."""

from __future__ import annotations

import difflib
import re
from typing import Any, Literal

import flet as ft

from iterthink import config
from iterthink.ai.ollama_util import chat_response_text

SemanticKind = Literal["STABLE", "NEW"]
RewriteVsMajor = Literal["rewritten", "major"]

# Ghost-preview highlights (ARGB ~14%): added stays light green, removed is light red + strikethrough.
_BG_NEW = "#252ECC71"
_BG_DEL = "#25FF5252"


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
    font_family: str | None = None,
    insert_color: str | None = None,
    insert_bgcolor: str | None = None,
    line_height: float | None = None,
) -> list[ft.TextSpan]:
    """Word-level inline diff: deletions (red + strikethrough), insertions (green tint / green text)."""
    ins_color = insert_color if insert_color is not None else ft.Colors.GREY_200
    ins_bg = insert_bgcolor if insert_bgcolor is not None else _BG_NEW
    style_kw: dict[str, Any] = {}
    if font_family:
        style_kw["font_family"] = font_family
    if line_height is not None:
        style_kw["height"] = line_height
    base = ft.TextStyle(size=base_size, color=base_color, **style_kw)
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
                            **style_kw,
                        ),
                    )
                )
        elif tag == "insert":
            chunk = "".join(b[j1:j2])
            if chunk:
                spans.append(
                    ft.TextSpan(
                        text=chunk,
                        style=ft.TextStyle(size=base_size, color=ins_color, bgcolor=ins_bg, **style_kw),
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
                            **style_kw,
                        ),
                    )
                )
            for t in b[j1:j2]:
                spans.append(
                    ft.TextSpan(
                        text=t,
                        style=ft.TextStyle(size=base_size, color=ins_color, bgcolor=ins_bg, **style_kw),
                    )
                )

    if not spans:
        return [ft.TextSpan(text=" ", style=base)]
    return spans


def _diff_style_kwargs(
    *,
    font_family: str | None,
    line_height: float | None,
) -> dict[str, Any]:
    style_kw: dict[str, Any] = {}
    if font_family:
        style_kw["font_family"] = font_family
    if line_height is not None:
        style_kw["height"] = line_height
    return style_kw


def build_old_side_spans(
    old_text: str,
    new_text: str,
    *,
    base_size: int = 12,
    base_color: str = ft.Colors.GREY_400,
    font_family: str | None = None,
    line_height: float | None = None,
) -> list[ft.TextSpan]:
    """Old side only: equal tokens (base) + deletions (red + strikethrough). Skips insertions."""
    style_kw = _diff_style_kwargs(font_family=font_family, line_height=line_height)
    base = ft.TextStyle(size=base_size, color=base_color, **style_kw)
    del_style = ft.TextStyle(
        size=base_size,
        color=base_color,
        bgcolor=_BG_DEL,
        decoration=ft.TextDecoration.LINE_THROUGH,
        decoration_color=ft.Colors.RED_200,
        **style_kw,
    )
    a = _tokenize(old_text)
    b = _tokenize(new_text)
    if not a and not b:
        return [ft.TextSpan(text=" ", style=base)]

    spans: list[ft.TextSpan] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            chunk = "".join(a[i1:i2])
            if chunk:
                spans.append(ft.TextSpan(text=chunk, style=base))
        elif tag == "delete":
            chunk = "".join(a[i1:i2])
            if chunk:
                spans.append(ft.TextSpan(text=chunk, style=del_style))
        elif tag == "replace":
            for t in a[i1:i2]:
                spans.append(ft.TextSpan(text=t, style=del_style))
        # insert: skipped (lives only on the new side)

    if not spans:
        return [ft.TextSpan(text=" ", style=base)]
    return spans


def build_new_side_spans(
    old_text: str,
    new_text: str,
    *,
    base_size: int = 12,
    base_color: str = ft.Colors.GREY_400,
    font_family: str | None = None,
    insert_color: str | None = None,
    insert_bgcolor: str | None = None,
    line_height: float | None = None,
) -> list[ft.TextSpan]:
    """New side only: equal tokens (base) + insertions (green). Skips deletions."""
    ins_color = insert_color if insert_color is not None else ft.Colors.GREY_200
    ins_bg = insert_bgcolor if insert_bgcolor is not None else _BG_NEW
    style_kw = _diff_style_kwargs(font_family=font_family, line_height=line_height)
    base = ft.TextStyle(size=base_size, color=base_color, **style_kw)
    ins_style = ft.TextStyle(size=base_size, color=ins_color, bgcolor=ins_bg, **style_kw)
    a = _tokenize(old_text)
    b = _tokenize(new_text)
    if not a and not b:
        return [ft.TextSpan(text=" ", style=base)]

    spans: list[ft.TextSpan] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            chunk = "".join(b[j1:j2])
            if chunk:
                spans.append(ft.TextSpan(text=chunk, style=base))
        elif tag == "insert":
            chunk = "".join(b[j1:j2])
            if chunk:
                spans.append(ft.TextSpan(text=chunk, style=ins_style))
        elif tag == "replace":
            for t in b[j1:j2]:
                spans.append(ft.TextSpan(text=t, style=ins_style))
        # delete: skipped (lives only on the old side)

    if not spans:
        return [ft.TextSpan(text=" ", style=base)]
    return spans


def _clip(s: str, max_len: int = 2800) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


async def judge_semantic(chat: Any, model: str, original: str, revised: str) -> SemanticKind:
    """Ask the configured chat backend whether the revision shifts intent (NEW) or not (STABLE)."""
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
        resp = await chat.chat(model=model, messages=messages, stream=False)
        raw = (chat_response_text(resp) or "").strip().upper()
        for token in raw.replace(",", " ").split():
            if token in ("NEW", "STABLE"):
                return "NEW" if token == "NEW" else "STABLE"
    except BaseException:
        pass
    return "STABLE"


async def judge_rewritten_vs_major(chat: Any, model: str, original: str, revised: str) -> RewriteVsMajor:
    """LLM tie-break: surface rewrite vs deeper semantic rewrite (rewritten)."""
    messages = [
        {
            "role": "system",
            "content": "Reply with exactly one word: rewritten or major. No punctuation.",
        },
        {
            "role": "user",
            "content": (
                "Compare paragraph A vs B after a heavy text edit.\n"
                "major = same core meaning and intent, mostly rephrase or reorder.\n"
                "rewritten = main point, recommendation, facts, or stance materially changed.\n\n"
                f"A:\n{_clip(original)}\n\nB:\n{_clip(revised)}"
            ),
        },
    ]
    try:
        resp = await chat.chat(model=model, messages=messages, stream=False)
        raw = (chat_response_text(resp) or "").strip().lower()
        for token in raw.replace(",", " ").split():
            if "rewritten" in token:
                return "rewritten"
            if token == "major" or token.startswith("major"):
                return "major"
    except BaseException:
        pass
    return "major"
