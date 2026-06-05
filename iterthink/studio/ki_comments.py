"""Pure helpers for KI sidebar paragraph comment list."""

from __future__ import annotations


def paragraph_comment_label(paragraph_index: int) -> str:
    return f"Paragraph {int(paragraph_index) + 1}"


def plan_comment_list_label(page_index: int, kind: str) -> str:
    label = "pin" if kind == "pin" else "cloud"
    return f"Page {int(page_index) + 1} · {label}"


def sorted_comment_rows(comments: dict[int, str]) -> list[tuple[int, str]]:
    """Non-empty comments sorted by paragraph index (0-based)."""
    rows = [(int(k), (v or "").strip()) for k, v in comments.items()]
    return sorted((pi, body) for pi, body in rows if body)
