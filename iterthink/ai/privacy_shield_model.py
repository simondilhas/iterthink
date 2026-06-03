"""Backward-compatible entry points for privacy-shield GGUF ensure/download."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from iterthink.ai.privacy_shield_gguf import (
    ensure_privacy_shield_gguf_sync,
    gguf_cache_path,
    is_gguf_ready,
)

ProgressCb = Callable[[float], None]


def ensure_privacy_shield_model_sync(on_progress: ProgressCb | None = None) -> Path:
    return ensure_privacy_shield_gguf_sync(on_progress)


__all__ = [
    "ensure_privacy_shield_model_sync",
    "gguf_cache_path",
    "is_gguf_ready",
]
