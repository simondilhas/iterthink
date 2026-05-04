"""Discover Ollama models suitable for chat vs embeddings (uses `show` capabilities when present)."""

from __future__ import annotations

import asyncio
from typing import Any


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
