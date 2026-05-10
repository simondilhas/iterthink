"""Parallel Impact-tab LLM analysis (version-scoped RAG + JSON per paragraph)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from iterthink.ai.ollama_util import chat_response_text
from iterthink.compare.paragraph_semantics import embed_texts_cached
from iterthink.db.session import session_scope
from iterthink.impact_checks import ImpactCheck
from iterthink.persistence import impact_annotations as impact_ann
from iterthink.services import impact_rag

_FENCE_PREFIX = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_FENCE_SUFFIX = re.compile(r"\s*```\s*$")

VALID_STATUSES = frozenset({"stable", "changed", "risk"})

ProgressCb = Callable[[int, dict | None, str | None], Awaitable[None] | None]

_persist_lock = asyncio.Lock()


def _impact_debug_llm_enabled() -> bool:
    v = (os.environ.get("ITERTHINK_DEBUG_IMPACT") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _impact_debug_print_llm(phase: str, model: str, messages: list[dict[str, Any]]) -> None:
    if not _impact_debug_llm_enabled():
        return
    lines = [
        "",
        "=" * 72,
        f"iterthink Impact LLM  [{phase}]  model={model!r}",
        "=" * 72,
    ]
    for msg in messages:
        role = str(msg.get("role", "?"))
        body = msg.get("content", "")
        if not isinstance(body, str):
            body = repr(body)
        if len(body) > 16000:
            body = body[:16000] + "\n… [truncated at 16000 chars]"
        lines.append(f"\n--- {role} ---\n{body}")
    print("\n".join(lines), file=sys.stderr, flush=True)


def _coerce_impact_json(text: str) -> dict | None:
    if not text:
        return None
    cleaned = _FENCE_PREFIX.sub("", text)
    cleaned = _FENCE_SUFFIX.sub("", cleaned).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def _normalize_paragraph_ref(para: Any) -> int | None:
    if isinstance(para, int):
        return para if para >= 1 else None
    if isinstance(para, float) and int(para) == para:
        ip = int(para)
        return ip if ip >= 1 else None
    return None


def _normalize_payload(raw: dict | None) -> tuple[dict | None, str | None]:
    if raw is None:
        return None, "Model did not return valid JSON."
    st = raw.get("status")
    if not isinstance(st, str) or str(st).strip() not in VALID_STATUSES:
        return None, "Invalid or missing status."

    co = raw.get("comment")
    if not isinstance(co, str) or not str(co).strip():
        alt = raw.get("summary")
        if isinstance(alt, str) and alt.strip():
            co = alt.strip()
        else:
            co = ""
    else:
        co = str(co).strip()

    ex = raw.get("explanation")
    if not isinstance(ex, str) or not ex.strip():
        return None, "Invalid or missing explanation."
    ex = ex.strip()

    refs_raw = raw.get("references")
    if refs_raw is None:
        norm_refs: list[dict[str, Any]] = []
    elif not isinstance(refs_raw, list):
        return None, "references must be a JSON array."
    else:
        norm_refs = []
        for item in refs_raw:
            if not isinstance(item, dict):
                return None, "Each references entry must be an object."
            doc = item.get("document")
            if not isinstance(doc, str) or not doc.strip():
                return None, "Each reference needs a non-empty document string."
            para = _normalize_paragraph_ref(item.get("paragraph"))
            if para is None:
                return None, "Each reference needs paragraph (1-based integer, as in CONTEXT headers)."
            ent: dict[str, Any] = {"document": doc.strip(), "paragraph": para}
            note = item.get("note")
            if isinstance(note, str) and note.strip():
                ent["note"] = note.strip()
            norm_refs.append(ent)

    details = {"explanation": ex, "references": norm_refs}
    out: dict[str, Any] = {
        "status": str(st).strip(),
        "comment": co,
        "details": details,
    }
    return out, None


async def _emit(cb: ProgressCb | None, idx: int, payload: dict | None, err: str | None) -> None:
    if cb is None:
        return
    res = cb(idx, payload, err)
    if hasattr(res, "__await__"):
        await res  # type: ignore[func-returns-value]


async def _run_one_paragraph(
    llm_chat: Any,
    *,
    model: str,
    check: ImpactCheck,
    paragraph: str,
    context: str,
) -> tuple[dict | None, str | None]:
    try:
        user_text = check.user_template.format(text=paragraph, context=context or "(no context)")
    except KeyError as e:
        return None, f"Template format error: {e}"
    system = check.system_prompt
    if context and context.strip() and context.strip() != "(no context)":
        system = (
            system.rstrip()
            + "\n\nProject file context is provided below the paragraph text. "
            "Use it to ground your evaluation: consider how this paragraph interacts "
            "with or affects the referenced project documents. "
            "Cite context using document basename and paragraph number from the CONTEXT headers."
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    _impact_debug_print_llm(f"paragraph check={check.id!r}", model, messages)
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
    raw = _coerce_impact_json(text)
    return _normalize_payload(raw)


async def run_impact_analysis(
    llm_chat: Any,
    *,
    model: str,
    check: ImpactCheck,
    conn: Any,
    target_document_id: int,
    target_version_id: int,
    context_document_ids: list[int],
    paragraphs: list[str],
    on_progress: ProgressCb | None = None,
    concurrency: int = 4,
    top_k: int = 3,
) -> list[dict | None]:
    """Run Impact check over each non-empty paragraph with bounded parallelism."""
    n = len(paragraphs)
    results: list[dict | None] = [None] * n
    sem = asyncio.Semaphore(max(1, concurrency))

    with session_scope() as s0:
        await impact_rag.ingest_latest_versions_for_document_ids(s0, conn, context_document_ids)
        labels = impact_rag.document_label_map(s0, context_document_ids)

    async def one(idx: int, text: str) -> None:
        if not text.strip():
            await _emit(on_progress, idx, None, None)
            return
        async with sem:
            try:
                vecs = await embed_texts_cached(conn, None, [text])
            except BaseException:  # noqa: BLE001
                vecs = [[]]
            vec = vecs[0] if vecs else []
            ctx = ""
            if vec:
                ctx = impact_rag.retrieve_context_by_document_ids(
                    vec, conn, context_document_ids, labels, top_k=top_k
                )
            payload, err = await _run_one_paragraph(
                llm_chat, model=model, check=check, paragraph=text, context=ctx
            )
            if payload is not None:
                det = payload.get("details")
                details_dict = det if isinstance(det, dict) else None
                async with _persist_lock:
                    with session_scope() as s:
                        impact_ann.upsert_model_result(
                            s,
                            document_id=target_document_id,
                            version_id=target_version_id,
                            paragraph_index=idx,
                            prompt_id=check.id,
                            status=str(payload["status"]),
                            comment=str(payload["comment"]),
                            details=details_dict,
                        )
            results[idx] = payload
            await _emit(on_progress, idx, payload, err)

    await asyncio.gather(*(one(i, paragraphs[i]) for i in range(n)))
    return results


async def run_impact_summary(
    llm_chat: Any,
    *,
    model: str,
    annotations: list[tuple[int, str, str, int]],
) -> tuple[str | None, str | None]:
    """One-shot summary from (paragraph_index, status, comment, ref_count) tuples."""
    if not annotations:
        return None, None
    lines = []
    for i, st, co, nref in annotations:
        tail = f" ({nref} ref)" if nref else ""
        lines.append(f"Paragraph {i + 1}: [{st}] {co}{tail}")
    joined = "\n".join(lines)
    system = (
        "You summarize impact review results for the author. "
        "Write a short coherent overview (plain text, no JSON): "
        "main risks, themes, and anything needing follow-up."
    )
    user = f"Annotations:\n{joined}"
    sum_messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    _impact_debug_print_llm("document summary", model, sum_messages)
    try:
        resp = await llm_chat.chat(
            model=model,
            messages=sum_messages,
            stream=False,
        )
    except BaseException as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    text = (chat_response_text(resp) or "").strip()
    return (text or None), None
