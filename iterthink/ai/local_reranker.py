"""Local ONNX cross-encoder reranker (FastEmbed) for workspace search."""

from __future__ import annotations

from typing import Any

from iterthink import config

_model: Any = None


def _default_model_name() -> str:
    return getattr(config, "RAG_RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")


def _get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    _model = TextCrossEncoder(model_name=_default_model_name())
    return _model


def prepare_runtime_reranker_sync() -> None:
    """Download/load reranker weights (no inference)."""
    _get_model()


def rerank_sync(query: str, documents: list[str]) -> list[float]:
    if not documents:
        return []
    encoder = _get_model()
    scores = list(encoder.rerank(query, documents))
    if len(scores) < len(documents):
        scores.extend([0.0] * (len(documents) - len(scores)))
    return [float(s) for s in scores[: len(documents)]]
