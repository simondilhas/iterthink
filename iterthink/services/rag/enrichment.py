"""Optional LLM enrichment for RAG chunks and search queries."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from iterthink.ai.ollama_util import chat_response_text

_ENRICH_SYSTEM = (
    "Summarize the paragraph in one sentence and write exactly 3 short questions it answers. "
    "Respond with JSON: {\"summary\": \"...\", \"questions\": [\"q1\", \"q2\", \"q3\"]}."
)

_QUERY_VARIANTS_SYSTEM = (
    "Rewrite the search query as 3 alternative questions a user might ask to find the same information. "
    "Respond with JSON: {\"questions\": [\"q1\", \"q2\", \"q3\"]}."
)


class LlmChat(Protocol):
    async def chat(
        self,
        *,
        model: str = "",
        messages: list[dict[str, str]] | None = None,
        stream: bool = False,
    ) -> Any: ...


def enrichment_allowed_for_tier(ki_tier: str, enrichment_mode: str) -> bool:
    if enrichment_mode == "skip":
        return False
    tier = (ki_tier or "").strip().lower()
    return tier in ("local", "company", "cloud")


def _extract_json_object(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _normalize_questions(raw: Any, *, count: int = 3) -> tuple[str, ...]:
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            if len(out) >= count:
                break
    while len(out) < count:
        out.append("")
    return tuple(out[:count])


async def enrich_child(
    *,
    raw: str,
    header: str,
    doc_title: str,
    llm: LlmChat,
    model: str,
) -> tuple[str, tuple[str, str, str]]:
    user = f"Document: {doc_title}\nSection: {header}\n\nParagraph:\n{raw.strip()}"
    resp = await llm.chat(
        model=model,
        messages=[
            {"role": "system", "content": _ENRICH_SYSTEM},
            {"role": "user", "content": user},
        ],
        stream=False,
    )
    obj = _extract_json_object(chat_response_text(resp))
    if obj is None:
        return "", ("", "", "")
    summary = str(obj.get("summary") or "").strip()
    questions = _normalize_questions(obj.get("questions"))
    return summary, questions  # type: ignore[return-value]


async def generate_query_variants(
    query: str,
    *,
    llm: LlmChat,
    model: str,
) -> tuple[str, str, str]:
    resp = await llm.chat(
        model=model,
        messages=[
            {"role": "system", "content": _QUERY_VARIANTS_SYSTEM},
            {"role": "user", "content": query.strip()},
        ],
        stream=False,
    )
    obj = _extract_json_object(chat_response_text(resp))
    if obj is None:
        return ("", "", "")
    return _normalize_questions(obj.get("questions"))  # type: ignore[return-value]
