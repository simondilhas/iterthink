"""Smoke tests for local vision embedding (skipped without ONNX model)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from iterthink.ai.local_embedding import embedded_models_root
from iterthink.ai.local_vision_embedding import (
    VISION_EMBEDDING_HF_REPO,
    _ensure_hf_snapshot,
    _snapshot_dir,
    embed_image_crop_sync,
)


@pytest.mark.skipif(
    not (_snapshot_dir(embedded_models_root()) / "onnx" / "model_quantized.onnx").is_file(),
    reason="nomic-embed-vision ONNX not cached",
)
def test_embed_image_crop_sync(tmp_path: Path) -> None:
    _ensure_hf_snapshot(_snapshot_dir(embedded_models_root()))
    png = tmp_path / "crop.png"
    Image.new("RGB", (64, 64), color=(128, 64, 32)).save(png)
    vec = embed_image_crop_sync(png)
    assert len(vec) == 768
    assert abs(sum(v * v for v in vec) ** 0.5 - 1.0) < 0.05


def test_hf_repo_constant() -> None:
    assert "nomic-embed-vision" in VISION_EMBEDDING_HF_REPO
