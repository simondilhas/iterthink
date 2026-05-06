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
from iterthink.ollama_util import chat_response_text
from iterthink.paragraph_align import compute_hash

# ProgressCb: invoked per paragraph after a result arrives (cache hit OR LLM).
# Args: (idx, payload_or_None, error_or_None).
ProgressCb = Callable[[int, dict | None, str | None], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load_cached(check_id: str, old: str, new: str, model: str) -> dict | None:
    """Look up a cached result by (check_id, old_sha, new_sha, model)."""
    old_h = compute_hash(old)
    new_h = compute_hash(new)
    with session_scope() as sess:
        row = (
            sess.query(ParagraphAnalysis)
            .filter(
                ParagraphAnalysis.check_id == check_id,
                ParagraphAnalysis.old_sha256 == old_h,
                ParagraphAnalysis.new_sha256 == new_h,
                ParagraphAnalysis.model == model,
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


def save_result(check_id: str, old: str, new: str, model: str, payload: dict) -> None:
    old_h = compute_hash(old)
    new_h = compute_hash(new)
    body = json.dumps(payload, ensure_ascii=False)
    with session_scope() as sess:
        existing = (
            sess.query(ParagraphAnalysis)
            .filter(
                ParagraphAnalysis.check_id == check_id,
                ParagraphAnalysis.old_sha256 == old_h,
                ParagraphAnalysis.new_sha256 == new_h,
                ParagraphAnalysis.model == model,
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
) -> tuple[dict | None, str | None]:
    """One LLM call (Ollama or routed HTTP). Returns ``(payload, error)``."""
    user_text = check.user_template.format(old=old or "", new=new or "")
    messages = [
        {"role": "system", "content": check.system_prompt},
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
) -> list[dict | None]:
    """Run ``check`` over each ``(old, new)`` pair.

    Skips paragraphs where both sides are blank (returns ``None``).
    When OLD and NEW match (same content hash), skips the LLM and uses a neutral
    synthetic payload (still written to cache when ``use_cache`` is true).
    Sequential to keep the backend responsive; the spinner-per-row UI
    gives immediate feedback. Uses cache unless ``use_cache=False`` (refresh).
    """
    results: list[dict | None] = [None] * len(pairs)
    for i, (old, new) in enumerate(pairs):
        if not (old or "").strip() and not (new or "").strip():
            await _emit(on_progress, i, None, None)
            continue
        payload: dict | None = None
        err: str | None = None
        if compute_hash(old) == compute_hash(new):
            payload = unchanged_paragraph_payload(check)
            if use_cache:
                try:
                    save_result(check.id, old, new, model, payload)
                except BaseException as exc:  # noqa: BLE001
                    err = f"Cache write failed: {type(exc).__name__}: {exc}"
            results[i] = payload
            await _emit(on_progress, i, payload, err)
            continue
        if use_cache:
            payload = load_cached(check.id, old, new, model)
        if payload is None:
            payload, err = await run_paragraph(
                llm_chat, model=model, check=check, old=old, new=new
            )
            if payload is not None:
                try:
                    save_result(check.id, old, new, model, payload)
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
