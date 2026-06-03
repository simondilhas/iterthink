"""Run privacy redaction with llama.cpp on the cached GGUF."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from iterthink.ai.privacy_shield_gguf import gguf_cache_path, is_gguf_ready

_llama: Any = None
_load_lock = asyncio.Lock()


def _get_llama() -> Any:
    global _llama
    if _llama is not None:
        return _llama
    if not is_gguf_ready():
        raise FileNotFoundError(
            f"Privacy shield model not found at {gguf_cache_path()}. Restart the app to download it."
        )
    try:
        from llama_cpp import Llama
    except ImportError as ex:
        py = sys.executable
        raise RuntimeError(
            "llama-cpp-python is not installed for the Python running this app "
            f"({py}). From the project folder, run:\n"
            f"  {py} -m pip install llama-cpp-python\n"
            "If you use a venv, activate it first, then restart Iterthink."
        ) from ex

    path = gguf_cache_path()
    _llama = Llama(
        model_path=str(path),
        n_ctx=4096,
        n_threads=0,
        verbose=False,
    )
    return _llama


def require_llama_cpp_import() -> None:
    """Fail fast with a clear message when the runtime Python lacks llama-cpp-python."""
    try:
        from llama_cpp import Llama  # noqa: F401
    except ImportError as ex:
        py = sys.executable
        raise RuntimeError(
            "llama-cpp-python is not installed for the Python running this app "
            f"({py}). From the project folder, run:\n"
            f"  {py} -m pip install llama-cpp-python\n"
            "If you use a venv, activate it first, then restart Iterthink."
        ) from ex


def reset_llama_cache() -> None:
    """Drop loaded model (e.g. after replacing GGUF on disk)."""
    global _llama
    _llama = None


def complete_redaction_json_sync(system: str, user: str) -> str:
    llm = _get_llama()
    out = llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=2048,
    )
    choice = (out.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return str(msg.get("content") or "")


async def complete_redaction_json(system: str, user: str) -> str:
    async with _load_lock:
        return await asyncio.to_thread(complete_redaction_json_sync, system, user)
