"""Bundled ONNX embeddings (FastEmbed) for paragraph compare — no Ollama."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np

from iterthink import config
from iterthink.persistence import store_db

# Cache keys / FastEmbed registry name (quantized ~0.13 GB).
LOCAL_EMBEDDING_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5-Q"
LOCAL_EMBEDDING_MODEL_ID = "nomic-embed-text-v1.5-Q"
NOMIC_DOCUMENT_PREFIX = "search_document:"

_model: Any = None


def bundled_embedding_models_root() -> Path:
    """Shipped with the wheel / Flet build (CI prefetch writes here for ``package_data``).

    Models live under ``iterthink/embedded_models/`` (sibling of the ``ai`` package), not
    inside ``iterthink/ai/``.
    """
    return Path(__file__).resolve().parent.parent / "embedded_models"


def embedded_models_root() -> Path:
    """Writable ONNX cache next to ``store.sqlite3`` (same ``store_dir`` as the app DB)."""
    return config.STORE_DIR / "embedded_models"


def _seed_runtime_cache_from_bundle() -> None:
    """If the store cache has no ONNX yet but the package ships weights, copy once (offline installers)."""
    dest = embedded_models_root()
    if dest.is_dir() and any(dest.rglob("*.onnx")):
        return
    src = bundled_embedding_models_root()
    if not src.is_dir() or not any(src.rglob("*.onnx")):
        return
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True, copy_function=shutil.copy2)


def nomic_document_text(text: str) -> str:
    """Nomic v1.5 expects task prefixes; keep one prefix for comparable cosines."""
    if text.startswith("search_query:") or text.startswith("search_document:"):
        return text
    return f"{NOMIC_DOCUMENT_PREFIX}{text}"


def _get_model() -> Any:
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        store_db.ensure_store_dir()
        _seed_runtime_cache_from_bundle()
        root = embedded_models_root()
        root.mkdir(parents=True, exist_ok=True)
        _model = TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL_NAME, cache_dir=str(root))
    return _model


def ensure_bundle_model_downloaded() -> None:
    """Download weights into the package tree (CI prefetch / packaging only)."""
    root = bundled_embedding_models_root()
    root.mkdir(parents=True, exist_ok=True)
    from fastembed import TextEmbedding

    TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL_NAME, cache_dir=str(root))


def ensure_model_downloaded() -> None:
    """Backward-compatible alias: same as :func:`ensure_bundle_model_downloaded`."""
    ensure_bundle_model_downloaded()


def embed_batch_sync(texts: list[str]) -> list[np.ndarray]:
    """Encode prefixed strings; returns float32 vectors (768-dim for Nomic v1.5)."""
    if not texts:
        return []
    model = _get_model()
    prefixed = [nomic_document_text(t) for t in texts]
    return list(model.embed(prefixed))
