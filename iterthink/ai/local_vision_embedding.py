"""Local ONNX vision embeddings (nomic-embed-vision-v1.5)."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np

from iterthink.ai.local_embedding import embedded_models_root
from iterthink.persistence import store_db

VISION_EMBEDDING_HF_REPO = "nomic-ai/nomic-embed-vision-v1.5"
VISION_EMBEDDING_MODEL_ID = "nomic-embed-vision-v1.5"
_VISION_SNAPSHOT_DIRNAME = "nomic-embed-vision-v1.5"

_session: Any = None
_preprocessor_cfg: dict[str, Any] | None = None


def _snapshot_dir(root: Path) -> Path:
    return root / _VISION_SNAPSHOT_DIRNAME


def _ensure_hf_snapshot(snapshot_root: Path) -> None:
    from huggingface_hub import snapshot_download

    onnx_path = snapshot_root / "onnx" / "model_quantized.onnx"
    preproc = snapshot_root / "preprocessor_config.json"
    if snapshot_root.is_dir() and onnx_path.is_file() and preproc.is_file():
        return
    snapshot_root.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=VISION_EMBEDDING_HF_REPO,
        local_dir=str(snapshot_root),
    )


def _load_preprocessor_cfg(snapshot_root: Path) -> dict[str, Any]:
    global _preprocessor_cfg
    if _preprocessor_cfg is not None:
        return _preprocessor_cfg
    path = snapshot_root / "preprocessor_config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    _preprocessor_cfg = data if isinstance(data, dict) else {}
    return _preprocessor_cfg


def _get_session() -> Any:
    global _session
    if _session is not None:
        return _session
    import onnxruntime as ort

    store_db.ensure_store_dir()
    root = embedded_models_root()
    root.mkdir(parents=True, exist_ok=True)
    snap = _snapshot_dir(root)
    _ensure_hf_snapshot(snap)
    _load_preprocessor_cfg(snap)
    onnx_path = snap / "onnx" / "model_quantized.onnx"
    _session = ort.InferenceSession(
        str(onnx_path),
        providers=["CPUExecutionProvider"],
    )
    return _session


def _preprocess_image(image: Path | bytes) -> np.ndarray:
    from PIL import Image

    cfg = _load_preprocessor_cfg(_snapshot_dir(embedded_models_root()))
    size = cfg.get("size") or {}
    h = int(size.get("height") or 224)
    w = int(size.get("width") or 224)
    mean = cfg.get("image_mean") or [0.485, 0.456, 0.406]
    std = cfg.get("image_std") or [0.229, 0.224, 0.225]

    if isinstance(image, (bytes, bytearray)):
        img = Image.open(BytesIO(image)).convert("RGB")
    else:
        img = Image.open(image).convert("RGB")
    img = img.resize((w, h), Image.Resampling.BILINEAR)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    mean_arr = np.array(mean, dtype=np.float32).reshape(1, 1, 3)
    std_arr = np.array(std, dtype=np.float32).reshape(1, 1, 3)
    arr = (arr - mean_arr) / std_arr
    return np.transpose(arr, (2, 0, 1))[np.newaxis, ...]


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(vec))
    if n < 1e-9:
        return vec
    return vec / n


def embed_image_crop_sync(image: Path | bytes) -> list[float]:
    """Encode one crop PNG; returns 768-d L2-normalized vector."""
    session = _get_session()
    batch = _preprocess_image(image)
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: batch})
    out = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
    if out.size > 768:
        out = out[:768]
    elif out.size < 768:
        padded = np.zeros(768, dtype=np.float32)
        padded[: out.size] = out
        out = padded
    out = _l2_normalize(out)
    return out.astype(np.float32).tolist()
