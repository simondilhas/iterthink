"""Run per-paragraph checks against Ollama with content-hash SQLite caching."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from iterthink.checks import Check, unchanged_paragraph_payload
from iterthink.db.models import ParagraphAnalysis
from iterthink.db.session import session_scope
from iterthink.ai.ollama_util import chat_response_text
from iterthink.compare.paragraph_align import compute_hash

# ProgressCb: invoked per paragraph after a result arrives (cache hit OR LLM).
# Args: (idx, payload_or_None, error_or_None).
ProgressCb = Callable[[int, dict | None, str | None], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cached(
    check_id: str,
    old: str,
    new: str,
    model: str,
    *,
    document_path_key: str = "",
) -> dict | None:
    """Look up a cached result by (check_id, old_sha, new_sha, model, document_path_key)."""
    old_h = compute_hash(old)
    new_h = compute_hash(new)
    pk = document_path_key or ""
    with session_scope() as sess:
        row = (
            sess.query(ParagraphAnalysis)
            .filter(
                ParagraphAnalysis.check_id == check_id,
                ParagraphAnalysis.old_sha256 == old_h,
                ParagraphAnalysis.new_sha256 == new_h,
                ParagraphAnalysis.model == model,
                ParagraphAnalysis.document_path_key == pk,
            )
            .order_by(ParagraphAnalysis.created_at.desc())
            .first()
        )
        if row is None:
            return None
        try:
            return json.loads(row.result_json)
        except (json.JSONDecodeError, TypeError):
            return None


def save_result(
    check_id: str,
    old: str,
    new: str,
    model: str,
    payload: dict,
    *,
    document_path_key: str = "",
) -> None:
    old_h = compute_hash(old)
    new_h = compute_hash(new)
    pk = document_path_key or ""
    body = json.dumps(payload, ensure_ascii=False)
    with session_scope() as sess:
        existing = (
            sess.query(ParagraphAnalysis)
            .filter(
                ParagraphAnalysis.check_id == check_id,
                ParagraphAnalysis.old_sha256 == old_h,
                ParagraphAnalysis.new_sha256 == new_h,
                ParagraphAnalysis.model == model,
                ParagraphAnalysis.document_path_key == pk,
            )
            .first()
        )
        if existing is not None:
            existing.result_json = body
            return
        sess.add(
            ParagraphAnalysis(
                check_id=check_id,
                old_sha256=old_h,
                new_sha256=new_h,
                model=model,
                document_path_key=pk,
                result_json=body,
            )
        )


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

# Strip stray markdown fences some models wrap around JSON despite format="json".
_FENCE_PREFIX = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_FENCE_SUFFIX = re.compile(r"\s*```\s*$")


def _coerce_json(text: str) -> dict | None:
    if not text:
        return None
    cleaned = _FENCE_PREFIX.sub("", text)
    cleaned = _FENCE_SUFFIX.sub("", cleaned).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first {...} block.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


async def run_paragraph(
    llm_chat: Any,
    *,
    model: str,
    check: Check,
    old: str,
    new: str,
    context: str = "",
) -> tuple[dict | None, str | None]:
    """One LLM call (Ollama or routed HTTP). Returns ``(payload, error)``."""
    template = check.user_template
    system = check.system_prompt
    if context:
        template = template + "\n\nCONTEXT FROM PROJECT FILES:\n{context}"
        system = (
            system.rstrip()
            + "\n\nWhen CONTEXT FROM PROJECT FILES is provided, use it to ground "
            "your evaluation in the broader project scope. Consider how this change "
            "interacts with or affects the referenced project documents."
        )
    user_text = template.format(old=old or "", new=new or "", context=context)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    try:
        resp = await llm_chat.chat(
            model=model,
            messages=messages,
            stream=False,
            format="json",
        )
    except BaseException as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    text = chat_response_text(resp) or ""
    payload = _coerce_json(text)
    if payload is None:
        return None, "Model did not return valid JSON."
    return payload, None


# ---------------------------------------------------------------------------
# Document-level driver
# ---------------------------------------------------------------------------

async def run_check_for_document(
    llm_chat: Any,
    *,
    model: str,
    check: Check,
    pairs: list[tuple[str, str]],
    on_progress: ProgressCb | None = None,
    use_cache: bool = True,
    context_per_pair: list[str | None] | None = None,
    document_path_key: str = "",
) -> list[dict | None]:
    """Run ``check`` over each ``(old, new)`` pair.

    Skips paragraphs where both sides are blank (returns ``None``).
    When OLD and NEW match (same content hash), skips the LLM and uses a neutral
    synthetic payload (still written to cache when ``use_cache`` is true).
    Sequential to keep the backend responsive; the spinner-per-row UI
    gives immediate feedback. Uses cache unless ``use_cache=False`` (refresh).

    When ``context_per_pair`` is provided, each non-empty context string is
    appended to the prompt for that paragraph and the result is not cached
    (context is not part of the existing cache key).
    """
    results: list[dict | None] = [None] * len(pairs)
    for i, (old, new) in enumerate(pairs):
        ctx = (
            (context_per_pair[i] if context_per_pair and i < len(context_per_pair) else None) or ""
        )
        if not (old or "").strip() and not (new or "").strip():
            await _emit(on_progress, i, None, None)
            continue
        payload: dict | None = None
        err: str | None = None
        if compute_hash(old) == compute_hash(new):
            payload = unchanged_paragraph_payload(check)
            if use_cache and not ctx:
                try:
                    save_result(
                        check.id, old, new, model, payload, document_path_key=document_path_key
                    )
                except BaseException as exc:  # noqa: BLE001
                    err = f"Cache write failed: {type(exc).__name__}: {exc}"
            results[i] = payload
            await _emit(on_progress, i, payload, err)
            continue
        # Skip cache when context is present (context not factored into cache key).
        if use_cache and not ctx:
            payload = load_cached(
                check.id, old, new, model, document_path_key=document_path_key
            )
        if payload is None:
            payload, err = await run_paragraph(
                llm_chat, model=model, check=check, old=old, new=new, context=ctx
            )
            if payload is not None and not ctx:
                try:
                    save_result(
                        check.id, old, new, model, payload, document_path_key=document_path_key
                    )
                except BaseException as exc:  # noqa: BLE001
                    err = err or f"Cache write failed: {type(exc).__name__}: {exc}"
        results[i] = payload
        await _emit(on_progress, i, payload, err)
    return results


async def _emit(cb: ProgressCb | None, idx: int, payload: dict | None, err: str | None) -> None:
    if cb is None:
        return
    res = cb(idx, payload, err)
    if hasattr(res, "__await__"):
        await res  # type: ignore[func-returns-value]
