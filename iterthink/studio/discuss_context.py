"""Discuss-mode LLM context: selection excerpt or full document."""

from __future__ import annotations


def discuss_llm_uses_selection(buf: str, selection_range: tuple[int, int] | None) -> bool:
    if selection_range is None:
        return False
    a, b = selection_range
    return 0 <= a < b <= len(buf) and bool(buf[a:b].strip())


def discuss_llm_context_text(
    buf: str,
    selection_range: tuple[int, int] | None,
    *,
    max_chars: int = 8000,
) -> str:
    """Return selection slice when range is non-empty and valid; otherwise full buffer (capped)."""
    if discuss_llm_uses_selection(buf, selection_range):
        a, b = selection_range  # type: ignore[misc]
        return buf[a:b].strip()[:max_chars]
    return (buf or "")[:max_chars]
