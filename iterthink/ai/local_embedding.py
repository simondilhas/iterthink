"""Bundled ONNX embeddings (FastEmbed) for paragraph compare — no Ollama."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from iterthink import config
from iterthink.persistence import store_db

# ONNX + tokenizer (HF); quantized single-file ``model_quantized.onnx``.
_LOCAL_EMBEDDING_HF_REPO = "onnx-community/gte-multilingual-base"
# Snapshot directory under each cache root (HF repo name tail).
_HF_SNAPSHOT_DIRNAME = _LOCAL_EMBEDDING_HF_REPO.split("/")[-1]

# Stable id for FastEmbed + sqlite cache keys (quantized ONNX ~0.35 GB).
LOCAL_EMBEDDING_MODEL_NAME = "onnx-community/gte-multilingual-base-q"
LOCAL_EMBEDDING_MODEL_ID = "gte-multilingual-base-q"

_model: Any = None


def _register_custom_embedding_model() -> None:
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    registered = {m["model"] for m in TextEmbedding.list_supported_models()}
    if LOCAL_EMBEDDING_MODEL_NAME in registered:
        return

    TextEmbedding.add_custom_model(
        model=LOCAL_EMBEDDING_MODEL_NAME,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=_LOCAL_EMBEDDING_HF_REPO),
        dim=768,
        model_file="onnx/model_quantized.onnx",
        additional_files=[],
        description=(
            "Alibaba GTE multilingual base (ONNX), mean-pool token embeddings + L2 norm; "
            "multilingual, long-context tokenizer."
        ),
        license="apache-2.0",
        size_in_gb=0.35,
    )


def _patch_tokenizer_config_json(path: Path) -> None:
    """FastEmbed requires numeric ``max_length``; some models ship ``null`` (see preprocessor_utils min())."""
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("max_length") is not None:
        return
    cap = 8192
    mml = data.get("model_max_length")
    if isinstance(mml, int) and 0 < mml < cap:
        cap = mml
    data["max_length"] = cap
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _snapshot_dir(parent: Path) -> Path:
    return parent / _HF_SNAPSHOT_DIRNAME


def _ensure_hf_snapshot(snapshot_root: Path) -> None:
    """Download ONNX + tokenizer into *snapshot_root* and patch tokenizer config for FastEmbed."""
    from huggingface_hub import snapshot_download

    tokenizer_cfg = snapshot_root / "tokenizer_config.json"
    onnx_path = snapshot_root / "onnx" / "model_quantized.onnx"
    special_map = snapshot_root / "special_tokens_map.json"
    if (
        snapshot_root.is_dir()
        and tokenizer_cfg.is_file()
        and onnx_path.is_file()
        and special_map.is_file()
    ):
        _patch_tokenizer_config_json(tokenizer_cfg)
        return

    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=_LOCAL_EMBEDDING_HF_REPO,
        local_dir=str(snapshot_root),
    )
    _patch_tokenizer_config_json(tokenizer_cfg)


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
    tok = _snapshot_dir(dest) / "tokenizer_config.json"
    _patch_tokenizer_config_json(tok)


def _get_model() -> Any:
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _register_custom_embedding_model()
        store_db.ensure_store_dir()
        _seed_runtime_cache_from_bundle()
        root = embedded_models_root()
        root.mkdir(parents=True, exist_ok=True)
        snap = _snapshot_dir(root)
        _ensure_hf_snapshot(snap)
        _model = TextEmbedding(
            model_name=LOCAL_EMBEDDING_MODEL_NAME,
            cache_dir=str(root),
            specific_model_path=str(snap),
        )
    return _model


def ensure_bundle_model_downloaded() -> None:
    """Download weights into the package tree (CI prefetch / packaging only)."""
    _register_custom_embedding_model()
    root = bundled_embedding_models_root()
    root.mkdir(parents=True, exist_ok=True)
    _ensure_hf_snapshot(_snapshot_dir(root))


def ensure_model_downloaded() -> None:
    """Backward-compatible alias: same as :func:`ensure_bundle_model_downloaded`."""
    ensure_bundle_model_downloaded()


def embed_batch_sync(texts: list[str]) -> list[np.ndarray]:
    """Encode strings; returns float32 vectors (768-dim)."""
    if not texts:
        return []
    model = _get_model()
    return list(model.embed(texts))
