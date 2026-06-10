"""Tiered import document-function suggestion (fast path + optional LLM excerpt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.contract.document_classification import (
    SuggestResult,
    build_classification_excerpt,
    suggest_document_function_fast,
    suggest_document_function_llm,
)
from iterthink.import_classification_settings import normalize_import_classification_tier


def _effective_import_model(studio: Any, tier: str) -> str:
    explicit = (getattr(config, "IMPORT_CLASSIFICATION_MODEL", "") or "").strip()
    if explicit:
        return explicit
    if tier == "local":
        return getattr(studio, "ollama_model", None) or config.DEFAULT_OLLAMA_MODEL
    backend = studio._make_llm_backend_for_tier(tier)
    return backend.effective_model(None)


async def suggest_for_import(
    studio: Any,
    *,
    src_path: Path,
    dest_md_path: Path,
    body: str | None = None,
) -> SuggestResult:
    fast = suggest_document_function_fast(
        src_path=src_path,
        dest_md_path=dest_md_path,
        body=body,
    )
    if not getattr(config, "IMPORT_CLASSIFICATION_LLM_ENABLED", False):
        return fast
    if fast.confidence == "high":
        return fast
    excerpt = build_classification_excerpt(
        body or "",
        max_chars=int(getattr(config, "IMPORT_CLASSIFICATION_EXCERPT_MAX_CHARS", 1200)),
    )
    if not excerpt.strip():
        return fast
    tier = normalize_import_classification_tier(
        getattr(config, "IMPORT_CLASSIFICATION_TIER", "local")
    )
    llm = studio._make_llm_backend_for_tier(tier)
    model = _effective_import_model(studio, tier)
    llm_result = await suggest_document_function_llm(
        llm,
        model=model,
        excerpt=excerpt,
    )
    return llm_result if llm_result is not None else fast
