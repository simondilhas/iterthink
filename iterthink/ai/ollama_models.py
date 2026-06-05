"""Discover Ollama models suitable for chat vs embeddings (uses `show` capabilities when present)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

_VISION_NAME_HINTS = re.compile(
    r"(llava|vision|moondream|bakllava|minicpm-v|llama3\.2-vision|gemma3)",
    re.IGNORECASE,
)


async def _capabilities_for(ollama: Any, name: str, sem: asyncio.Semaphore) -> tuple[str, frozenset[str]]:
    async with sem:
        try:
            sh = await ollama.show(name)
            raw = getattr(sh, "capabilities", None) or []
            return name, frozenset(str(x) for x in raw)
        except BaseException:
            return name, frozenset()


async def classify_installed_models(ollama: Any) -> tuple[list[str], list[str]]:
    """
    Returns (chat_model_names, embedding_model_names), sorted unique.
    """
    lr = await ollama.list()
    names = sorted({str(m.model) for m in lr.models if getattr(m, "model", None)})
    if not names:
        return [], []

    sem = asyncio.Semaphore(8)
    pairs = await asyncio.gather(*[_capabilities_for(ollama, n, sem) for n in names])
    cap_map = dict(pairs)

    embed: list[str] = []
    chat: list[str] = []
    for n in names:
        caps = cap_map[n]
        if "embedding" in caps:
            embed.append(n)
        if "completion" in caps or "tools" in caps:
            chat.append(n)

    if not embed:
        for n in names:
            low = n.lower()
            if "embed" in low or "bge-" in low or low.startswith("mxbai-embed"):
                embed.append(n)

    if not chat:
        chat = [n for n in names if n not in embed or "embed" not in n.lower()]

    if not chat:
        chat = list(names)

    return sorted(set(chat)), sorted(set(embed))


def _base_model_name(name: str) -> str:
    return name.split(":", 1)[0].strip()


def model_name_installed(installed: list[str], wanted: str) -> bool:
    """True if *wanted* matches an installed tag (``llava`` ↔ ``llava:latest``)."""
    w = wanted.strip()
    if not w:
        return False
    wb = _base_model_name(w)
    for n in installed:
        if n == w or _base_model_name(n) == wb:
            return True
    return False


def _looks_like_vision_model(name: str, caps: frozenset[str]) -> bool:
    if "vision" in caps:
        return True
    return bool(_VISION_NAME_HINTS.search(name))


async def classify_vision_models(ollama: Any) -> list[str]:
    """Return installed model names that support image input."""
    lr = await ollama.list()
    names = sorted({str(m.model) for m in lr.models if getattr(m, "model", None)})
    if not names:
        return []

    sem = asyncio.Semaphore(8)
    pairs = await asyncio.gather(*[_capabilities_for(ollama, n, sem) for n in names])
    cap_map = dict(pairs)

    vision: list[str] = []
    for n in names:
        if _looks_like_vision_model(n, cap_map.get(n, frozenset())):
            vision.append(n)
    return sorted(set(vision))


def vision_model_names(installed: list[str], cap_map: dict[str, frozenset[str]] | None = None) -> list[str]:
    """Heuristic vision list from names (+ optional capability map)."""
    caps = cap_map or {}
    out: list[str] = []
    for n in installed:
        if _looks_like_vision_model(n, caps.get(n, frozenset())):
            out.append(n)
    return sorted(set(out))
