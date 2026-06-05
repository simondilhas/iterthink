"""Download and cache the privacy-shield GGUF from Hugging Face (no Ollama)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from iterthink import config

ProgressCb = Callable[[float], None]

_PRIVACY_DIRNAME = "privacy_shield"


def privacy_shield_dir() -> Path:
    root = config.STORE_DIR / _PRIVACY_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def gguf_cache_path() -> Path:
    return privacy_shield_dir() / config.PRIVACY_SHIELD_CACHE_NAME


def is_gguf_ready() -> bool:
    path = gguf_cache_path()
    return path.is_file() and path.stat().st_size > 0


def _hf_download_url() -> str:
    from huggingface_hub import hf_hub_url

    return hf_hub_url(
        config.PRIVACY_SHIELD_HF_REPO,
        config.PRIVACY_SHIELD_HF_FILE,
        repo_type="model",
    )


def _auth_headers() -> dict[str, str]:
    token = (os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def download_privacy_shield_gguf_sync(
    on_progress: ProgressCb | None = None,
    *,
    force: bool = False,
) -> Path:
    """
    Stream-download the GGUF into the store cache (atomic replace via ``.part`` file).
    ``on_progress`` receives fraction 0.0–1.0; may be called from a worker thread.
    """
    dest = gguf_cache_path()
    if not force and is_gguf_ready():
        if on_progress is not None:
            on_progress(1.0)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    url = _hf_download_url()
    headers = _auth_headers()

    if part.is_file():
        part.unlink()

    downloaded = 0
    total: int | None = None

    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=600.0) as resp:
        resp.raise_for_status()
        cl = resp.headers.get("content-length")
        if cl and cl.isdigit():
            total = int(cl)
        with part.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                if on_progress is not None:
                    if total and total > 0:
                        on_progress(min(1.0, downloaded / total))
                    elif downloaded > 0:
                        on_progress(0.0)

    if total and downloaded < total:
        part.unlink(missing_ok=True)
        raise OSError(f"Incomplete download ({downloaded} of {total} bytes).")

    part.replace(dest)
    if on_progress is not None:
        on_progress(1.0)
    return dest


def ensure_privacy_shield_gguf_sync(on_progress: ProgressCb | None = None) -> Path:
    """Download the GGUF when missing; no-op when cache file exists."""
    return download_privacy_shield_gguf_sync(on_progress, force=False)
