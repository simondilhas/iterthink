"""Local PII redaction before Office/Cloud LLM calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from iterthink.ai.ollama_util import chat_response_text
from iterthink.ai.privacy_shield_llm import complete_redaction_json
from iterthink import config
from iterthink.privacy_shield_settings import (
    build_redact_system_prompt,
    format_placeholder,
    llm_categories,
    placeholder_prefix_for_entity,
    regex_redact_configured,
)


def last_user_message_content(messages: list[dict[str, str]]) -> str:
    for m in reversed(messages):
        if str(m.get("role") or "") == "user":
            return str(m.get("content") or "")
    return ""


def privacy_shield_applies_to_tier(tier: str) -> bool:
    """True when outbound Office/Cloud requests are redacted."""
    t = (tier or "").strip().lower()
    return bool(config.PRIVACY_SHIELD_ENABLED) and t in ("company", "cloud")


def should_show_masked_in_chat(tier: str) -> bool:
    return privacy_shield_applies_to_tier(tier) and bool(config.PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT)

# Legacy reinject for maps created before {{ }} placeholders.
_LEGACY_PLACEHOLDER_RE = re.compile(r"<<([A-Z_]+)_(\d+)>>")


@dataclass
class RedactionMap:
    """placeholder -> original value (longest keys first for reinject)."""

    _entries: dict[str, str] = field(default_factory=dict)

    def add(self, placeholder: str, value: str) -> None:
        ph = (placeholder or "").strip()
        val = (value or "").strip()
        if ph and val:
            self._entries[ph] = val

    def merge_mapping(self, other: dict[str, str] | None) -> None:
        if other:
            for k, v in other.items():
                self.add(k, v)

    def merge(self, other: RedactionMap | None) -> None:
        if other is not None:
            self._entries.update(other._entries)

    def items_longest_first(self) -> list[tuple[str, str]]:
        return sorted(self._entries.items(), key=lambda kv: len(kv[0]), reverse=True)

    def __bool__(self) -> bool:
        return bool(self._entries)


async def redact_messages_for_tier(
    messages: list[dict[str, str]],
    tier: str,
) -> tuple[list[dict[str, str]], RedactionMap | None]:
    """Redact all message bodies when shield applies to this tier; else passthrough."""
    if not privacy_shield_applies_to_tier(tier):
        return messages, None
    redacted, rmap = await redact_messages(messages)
    return redacted, rmap


def reinject_text(text: str, rmap: RedactionMap | None) -> str:
    if not text or not rmap:
        return text
    out = text
    for ph, val in rmap.items_longest_first():
        out = out.replace(ph, val)
    # Legacy <<TOKEN_n>> in model output
    def _legacy_sub(m: re.Match[str]) -> str:
        key = format_placeholder(m.group(1), int(m.group(2)))
        return rmap._entries.get(key, m.group(0))

    return _LEGACY_PLACEHOLDER_RE.sub(_legacy_sub, out)


_GENERIC_REDACTED_RE = re.compile(r"\{\{REDACTED(?:_\d+)?\}\}", re.IGNORECASE)

_CHAR_SLICE_OVERLAP = 200


def split_redaction_chunks(
    text: str,
    max_chars: int,
    overlap_paragraphs: int = 1,
) -> list[str]:
    """Split text for LLM redaction: pack ``\\n\\n`` paragraphs, overlap, char-slice huge blocks."""
    if not text:
        return []
    max_chars = max(1, max_chars)
    if len(text) <= max_chars:
        return [text]

    raw_parts = text.split("\n\n")
    paras: list[str] = []
    for i, part in enumerate(raw_parts):
        block = part + ("\n\n" if i < len(raw_parts) - 1 else "")
        if len(block) <= max_chars:
            paras.append(block)
        else:
            paras.extend(_char_slices(block, max_chars, _CHAR_SLICE_OVERLAP))

    if not paras:
        return [text]

    overlap_n = max(0, overlap_paragraphs)
    chunks: list[str] = []
    buf: list[str] = []

    def _join(parts: list[str]) -> str:
        return "".join(parts)

    for para in paras:
        if not buf:
            buf = [para]
            continue
        if len(_join(buf + [para])) <= max_chars:
            buf.append(para)
            continue
        chunks.append(_join(buf))
        buf = buf[-overlap_n:] + [para] if overlap_n else [para]
        while len(buf) > 1 and len(_join(buf)) > max_chars:
            buf = buf[1:]
    if buf:
        chunks.append(_join(buf))
    return chunks if chunks else [text]


def _char_slices(text: str, max_chars: int, overlap: int) -> list[str]:
    """Hard-split a single oversized block with tail overlap between slices."""
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    start = 0
    step = max(1, max_chars - max(0, overlap))
    while start < len(text):
        out.append(text[start : start + max_chars])
        if start + max_chars >= len(text):
            break
        start += step
    return out


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Parse JSON from model output (raw object, fenced block, or first ``{...}`` in prose)."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty redaction response")
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = json.JSONDecoder().raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("redaction response is not valid JSON")


def _parse_redaction_json(raw: str) -> tuple[str, RedactionMap, list[dict[str, Any]]]:
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        raise ValueError("redaction JSON must be an object")
    redacted = str(data.get("redacted_text") or "")
    raw_entities = data.get("entities") or []
    entities: list[dict[str, Any]] = []
    rmap = RedactionMap()
    if isinstance(raw_entities, list):
        for ent in raw_entities:
            if not isinstance(ent, dict):
                continue
            entities.append(ent)
            ph = str(ent.get("placeholder") or "").strip()
            val = str(ent.get("value") or "").strip()
            if ph and val and not _GENERIC_REDACTED_RE.fullmatch(ph):
                rmap.add(ph, val)
    return redacted, rmap, entities


def apply_llm_entities_to_text(
    text: str, entities: list[dict[str, Any]]
) -> tuple[str, dict[str, str]]:
    """Replace entity values with typed {{PREFIX_n}} on regex-preprocessed text."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    work = text
    sorted_ents = sorted(
        entities,
        key=lambda e: len(str(e.get("value") or "")),
        reverse=True,
    )
    for ent in sorted_ents:
        val = str(ent.get("value") or "").strip()
        if not val or val not in work:
            continue
        prefix = placeholder_prefix_for_entity(
            str(ent.get("type") or ""),
            str(ent.get("placeholder") or ""),
        )
        n = counters.get(prefix, 0) + 1
        counters[prefix] = n
        ph = format_placeholder(prefix, n)
        mapping[ph] = val
        work = work.replace(val, ph)
    return work, mapping


def _dedupe_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep first entity per exact value (longest value wins on collision)."""
    by_val: dict[str, dict[str, Any]] = {}
    for ent in sorted(entities, key=lambda e: len(str(e.get("value") or "")), reverse=True):
        val = str(ent.get("value") or "").strip()
        if val and val not in by_val:
            by_val[val] = ent
    return list(by_val.values())


def _entity_values_still_present(text: str, entities: list[dict[str, Any]]) -> bool:
    for ent in entities:
        val = str(ent.get("value") or "").strip()
        if val and val in text:
            return True
    return False


def _redacted_llm_usable(redacted_llm: str) -> bool:
    return bool(redacted_llm.strip()) and "{{" in redacted_llm


async def _llm_collect_entities(regex_text: str, system: str) -> tuple[list[dict[str, Any]], str]:
    """Run local Qwen on full text or chunks; return merged entities and optional model redacted_text."""
    max_chars = config.PRIVACY_SHIELD_CHUNK_MAX_CHARS
    overlap = config.PRIVACY_SHIELD_CHUNK_OVERLAP_PARAGRAPHS

    if len(regex_text) <= max_chars:
        chunks = [regex_text]
    else:
        chunks = split_redaction_chunks(regex_text, max_chars, overlap)

    all_entities: list[dict[str, Any]] = []
    redacted_llm = ""
    for chunk in chunks:
        try:
            raw = (await complete_redaction_json(system, chunk)).strip()
        except BaseException as ex:
            raise ValueError(
                "Privacy shield could not redact; wait for the model download or disable shield in Settings. "
                f"{type(ex).__name__}: {ex}"
            ) from ex
        try:
            chunk_redacted, _, entities = _parse_redaction_json(raw)
        except ValueError as ex:
            raise ValueError(
                "Privacy shield could not redact; wait for the model download or disable shield in Settings. "
                f"{ex}"
            ) from ex
        all_entities.extend(entities)
        if len(chunks) == 1 and _redacted_llm_usable(chunk_redacted):
            redacted_llm = chunk_redacted
    return _dedupe_entities(all_entities), redacted_llm


async def redact_text_via_local_llm(text: str) -> tuple[str, RedactionMap]:
    """Regex pass for configured categories, then local Qwen for LLM categories."""
    if not (text or "").strip():
        return text, RedactionMap()

    regex_text, regex_map = regex_redact_configured(text)
    rmap = RedactionMap()
    rmap.merge_mapping(regex_map)

    if not regex_text.strip():
        return regex_text, rmap

    if not llm_categories():
        return regex_text, rmap

    system = build_redact_system_prompt()
    entities, redacted_llm = await _llm_collect_entities(regex_text, system)

    if entities:
        redacted, entity_map = apply_llm_entities_to_text(regex_text, entities)
        rmap.merge_mapping(entity_map)
        if _entity_values_still_present(redacted, entities) and _redacted_llm_usable(redacted_llm):
            redacted = redacted_llm
    elif _redacted_llm_usable(redacted_llm):
        redacted = redacted_llm
    else:
        redacted = regex_text
    return redacted, rmap


async def redact_messages(
    messages: list[dict[str, str]],
) -> tuple[list[dict[str, str]], RedactionMap]:
    """Redact content fields in all messages; merge maps."""
    merged = RedactionMap()
    out: list[dict[str, str]] = []
    for m in messages:
        role = str(m.get("role") or "user")
        content = str(m.get("content") or "")
        if content.strip():
            redacted, rmap = await redact_text_via_local_llm(content)
            merged.merge(rmap)
            out.append({"role": role, "content": redacted})
        else:
            out.append({"role": role, "content": content})
    return out, merged


def reinject_response(resp: Any, rmap: RedactionMap | None) -> Any:
    """Restore originals in a chat response dict or Ollama response object."""
    if not rmap or resp is None:
        return resp
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict) and "content" in msg:
            new_msg = dict(msg)
            new_msg["content"] = reinject_text(str(msg.get("content") or ""), rmap)
            return {**resp, "message": new_msg}
        return resp
    msg = getattr(resp, "message", None)
    if msg is not None:
        content = reinject_text(chat_response_text(resp), rmap)
        if isinstance(msg, dict):
            return {"message": {**msg, "content": content}}
        try:
            msg.content = content
        except Exception:
            pass
    return resp
