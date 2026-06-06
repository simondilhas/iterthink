"""Format retrieved RAG chunks for LLM prompts."""

from __future__ import annotations

import re

from iterthink import config

from .chunk_type import ChunkType

_RAG_CTX_START = re.compile(r"<!--\s*iterthink-rag-context-start\s*-->", re.IGNORECASE)
_RAG_CTX_END = re.compile(r"<!--\s*iterthink-rag-context-end\s*-->", re.IGNORECASE)
_TRIVIAL_HEADING_CHUNK = re.compile(r"^#{1,6}\s*[\d\-–—.]+\s*$", re.MULTILINE)


def rag_chunk_display_body(chunk: str) -> str:
    s = chunk.strip()
    start_m = _RAG_CTX_START.search(s)
    if not start_m:
        return s
    tail = s[start_m.end() :]
    end_m = _RAG_CTX_END.search(tail)
    if not end_m:
        return s
    body = tail[end_m.end() :].strip()
    return body if body else s


def chunk_usable_for_norm_context(chunk_full: str) -> bool:
    s = rag_chunk_display_body(chunk_full).strip()
    if len(s) < 20:
        return False
    if _TRIVIAL_HEADING_CHUNK.fullmatch(s):
        return False
    if len(s) > 60 and s.count(".") / len(s) > 0.22:
        return False
    if re.search(r"(?:\.\s*){6,}\d", s):
        return False
    return True


def chunk_usable_for_context(chunk_full: str, *, strict: bool) -> bool:
    s = rag_chunk_display_body(chunk_full).strip()
    if not s:
        return False
    if strict:
        return chunk_usable_for_norm_context(chunk_full)
    return True


def _context_max_chars() -> int:
    return max(200, int(getattr(config, "RAG_CONTEXT_MAX_CHARS", 2400)))


def _select_context_body(*, parent_text: str, raw_text: str, strict_filter: bool) -> str:
    parent = (parent_text or "").strip()
    raw = (raw_text or "").strip()
    if parent and chunk_usable_for_context(parent, strict=strict_filter):
        return rag_chunk_display_body(parent).strip()
    if raw and chunk_usable_for_context(raw, strict=strict_filter):
        return rag_chunk_display_body(raw).strip()
    return ""


def format_rag_context_block(
    *,
    fname: str,
    doc_title: str = "",
    section_header: str = "",
    parent_text: str = "",
    raw_text: str = "",
    slot_index: int,
    chunk_type: ChunkType,
    max_chars: int | None = None,
    strict_filter: bool = True,
) -> str | None:
    snip = _select_context_body(
        parent_text=parent_text, raw_text=raw_text, strict_filter=strict_filter
    )
    if not snip:
        return None
    cap = max_chars if max_chars is not None else _context_max_chars()
    if len(snip) > cap:
        snip = snip[: cap - 1] + "…"
    para_num = slot_index + 1
    header_bits = [f"[{fname}]"]
    if doc_title.strip():
        header_bits.append(f"title={doc_title.strip()}")
    if section_header.strip():
        header_bits.append(f"section={section_header.strip()}")
    header_bits.append(f"paragraph={para_num}")
    header_bits.append(f"type={chunk_type.value}")
    return " ".join(header_bits) + f"\n{snip}"


def format_override_context_block(
    *,
    paragraph_index: int,
    status: str,
    override_comment: str,
    embed_text: str,
    max_chars: int | None = None,
) -> str | None:
    comment = (override_comment or "").strip()
    body = (embed_text or "").strip()
    if not comment and not body:
        return None
    cap = max_chars if max_chars is not None else _context_max_chars()
    snip = body
    if len(snip) > cap:
        snip = snip[: cap - 1] + "…"
    header = (
        f"[PRIOR REVIEW] paragraph={paragraph_index + 1} "
        f"human_status={status}"
    )
    if comment:
        return f"{header}\nrecommendation={comment}\n{snip}"
    return f"{header}\n{snip}"
