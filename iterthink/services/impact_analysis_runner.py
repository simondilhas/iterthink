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
from iterthink.services import impact_prefilter, impact_rag

_FENCE_PREFIX = re.compile(r"^\s*```(?:json)?\s*", re.IGNORECASE)
_FENCE_SUFFIX = re.compile(r"\s*```\s*$")

VALID_STATUSES = frozenset({"stable", "changed", "risk"})

FINDINGS_CHECK_IDS = frozenset(
    {
        "norm_compliance",
        "impact_consistency",
        "scope_completeness",
        "risk_assessment",
        "design_intent",
    }
)

FINDINGS_PARAGRAPH_STATUSES = frozenset({"ok", "warning", "error", "not_applicable"})

NORM_TYPE_SEVERITY: dict[str, str] = {
    "ok": "info",
    "value_deviation": "error",
    "missing_ref": "warning",
    "wrong_ref": "error",
    "outdated_ref": "warning",
    "contradiction": "error",
    "unverifiable": "info",
}

CONSISTENCY_TYPE_SEVERITY: dict[str, str] = {
    "ok": "info",
    "contradiction": "error",
    "drift": "warning",
    "duplicate": "warning",
    "orphan": "warning",
    "unverifiable": "info",
}

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


def _as_bool(v: Any, *, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    return default


def _nullable_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    return None


def _require_str(v: Any, label: str) -> tuple[str | None, str | None]:
    if not isinstance(v, str) or not v.strip():
        return None, f"Finding needs non-empty {label}."
    return v.strip(), None


def _derive_findings_paragraph_status(
    not_applicable_reason: str | None,
    findings: list[dict[str, Any]],
) -> str:
    na = (not_applicable_reason or "").strip()
    if na:
        return "not_applicable"
    if not findings:
        return "ok"
    for f in findings:
        if f.get("severity") == "error":
            return "error"
    for f in findings:
        if f.get("severity") == "warning":
            return "warning"
    return "ok"


def _findings_comment(
    not_applicable_reason: str | None,
    findings: list[dict[str, Any]],
    derived_status: str,
) -> str:
    na = (not_applicable_reason or "").strip()
    if derived_status == "not_applicable" and na:
        return na[:200] + ("…" if len(na) > 200 else "")
    for sev in ("error", "warning"):
        for f in findings:
            if f.get("severity") == sev:
                act = f.get("action")
                if isinstance(act, str) and act.strip():
                    return act.strip()[:200] + ("…" if len(act.strip()) > 200 else "")
    if findings:
        return f"{len(findings)} finding(s)"
    return "OK"


def _normalize_norm_finding(raw: dict[str, Any], idx: int) -> tuple[dict[str, Any] | None, str | None]:
    ftype = raw.get("type")
    if not isinstance(ftype, str) or ftype.strip() not in NORM_TYPE_SEVERITY:
        return None, f"findings[{idx}]: invalid type."
    ftype = ftype.strip()
    exp_sev = NORM_TYPE_SEVERITY[ftype]
    sev = raw.get("severity")
    if not isinstance(sev, str) or sev.strip() != exp_sev:
        return None, f"findings[{idx}]: severity must be {exp_sev!r} for type {ftype!r}."
    sev = sev.strip()
    claim, err = _require_str(raw.get("claim"), "claim")
    if err:
        return None, f"findings[{idx}]: {err}"
    act, err = _require_str(raw.get("action"), "action")
    if err:
        return None, f"findings[{idx}]: {err}"

    norm_ref = _nullable_str(raw.get("norm_ref"))
    expected = _nullable_str(raw.get("expected"))
    found = _nullable_str(raw.get("found"))
    src_doc = _nullable_str(raw.get("source_document"))
    src_ex = _nullable_str(raw.get("source_excerpt"))

    if ftype in ("missing_ref", "unverifiable"):
        pass  # source_* may be null
    else:
        if src_doc is None or src_ex is None:
            return None, f"findings[{idx}]: source_document and source_excerpt required for type {ftype!r}."

    out: dict[str, Any] = {
        "type": ftype,
        "severity": sev,
        "claim": claim,
        "norm_ref": norm_ref,
        "expected": expected,
        "found": found,
        "action": act,
        "source_document": src_doc,
        "source_excerpt": src_ex,
    }
    return out, None


def _normalize_consistency_finding(raw: dict[str, Any], idx: int) -> tuple[dict[str, Any] | None, str | None]:
    ftype = raw.get("type")
    if not isinstance(ftype, str) or ftype.strip() not in CONSISTENCY_TYPE_SEVERITY:
        return None, f"findings[{idx}]: invalid type."
    ftype = ftype.strip()
    exp_sev = CONSISTENCY_TYPE_SEVERITY[ftype]
    sev = raw.get("severity")
    if not isinstance(sev, str) or sev.strip() != exp_sev:
        return None, f"findings[{idx}]: severity must be {exp_sev!r} for type {ftype!r}."
    sev = sev.strip()
    claim, err = _require_str(raw.get("claim"), "claim")
    if err:
        return None, f"findings[{idx}]: {err}"
    act, err = _require_str(raw.get("action"), "action")
    if err:
        return None, f"findings[{idx}]: {err}"

    this_states = _nullable_str(raw.get("this_states"))
    context_states = _nullable_str(raw.get("context_states"))
    src_doc = _nullable_str(raw.get("source_document"))
    src_ex = _nullable_str(raw.get("source_excerpt"))

    if ftype in ("orphan", "unverifiable"):
        pass
    else:
        if src_doc is None or src_ex is None:
            return None, f"findings[{idx}]: source_document and source_excerpt required for type {ftype!r}."

    out: dict[str, Any] = {
        "type": ftype,
        "severity": sev,
        "claim": claim,
        "this_states": this_states,
        "context_states": context_states,
        "source_document": src_doc,
        "source_excerpt": src_ex,
        "action": act,
    }
    return out, None


def _normalize_findings_envelope(
    raw: dict | None,
    *,
    check: ImpactCheck,
) -> tuple[dict | None, str | None]:
    if raw is None:
        return None, "Model did not return valid JSON."

    na_raw = raw.get("not_applicable_reason")
    na_reason = _nullable_str(na_raw) if na_raw is not None else None

    findings_raw = raw.get("findings")
    if not isinstance(findings_raw, list):
        return None, "findings must be a JSON array."
    findings_in: list[Any] = list(findings_raw)

    if na_reason:
        if findings_in:
            return None, "not_applicable_reason set but findings is not empty."
    low = _as_bool(raw.get("low_confidence"), default=False)

    norm_mode = check.id == "norm_compliance"
    findings_out: list[dict[str, Any]] = []
    for i, item in enumerate(findings_in):
        if not isinstance(item, dict):
            return None, f"findings[{i}] must be an object."
        if norm_mode:
            one, err = _normalize_norm_finding(item, i)
        else:
            one, err = _normalize_consistency_finding(item, i)
        if err or one is None:
            return None, err or "Invalid finding."
        findings_out.append(one)

    derived = _derive_findings_paragraph_status(na_reason, findings_out)

    reported = raw.get("paragraph_status")
    reported_str: str | None = None
    if isinstance(reported, str) and reported.strip() in FINDINGS_PARAGRAPH_STATUSES:
        reported_str = reported.strip()

    details: dict[str, Any] = {
        "low_confidence": low,
        "not_applicable_reason": na_reason,
        "findings": findings_out,
    }
    if reported_str is not None and reported_str != derived:
        details["paragraph_status_reported"] = reported_str

    comment = _findings_comment(na_reason, findings_out, derived)
    out: dict[str, Any] = {
        "status": derived,
        "comment": comment,
        "details": details,
    }
    return out, None


def _normalize_legacy_payload(raw: dict | None) -> tuple[dict | None, str | None]:
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


def _normalize_payload(
    raw: dict | None,
    *,
    check: ImpactCheck | None = None,
) -> tuple[dict | None, str | None]:
    if check is not None and check.id in FINDINGS_CHECK_IDS:
        return _normalize_findings_envelope(raw, check=check)
    return _normalize_legacy_payload(raw)


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
    if (
        check.id not in FINDINGS_CHECK_IDS
        and context
        and context.strip()
        and context.strip() != "(no context)"
    ):
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
    return _normalize_payload(raw, check=check)


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
            if check.id == "norm_compliance":
                skipped = impact_prefilter.norm_compliance_skip_llm(text)
                if skipped is not None:
                    async with _persist_lock:
                        with session_scope() as s:
                            impact_ann.upsert_model_result(
                                s,
                                document_id=target_document_id,
                                version_id=target_version_id,
                                paragraph_index=idx,
                                prompt_id=check.id,
                                status=str(skipped["status"]),
                                comment=str(skipped["comment"]),
                                details=skipped.get("details")
                                if isinstance(skipped.get("details"), dict)
                                else None,
                            )
                    results[idx] = skipped
                    await _emit(on_progress, idx, skipped, None)
                    return
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
