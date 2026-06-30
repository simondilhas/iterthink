"""Pure helpers for KI sidebar paragraph comment list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

KI_COMMENTS_GROUP_USER = "user"
KI_COMMENTS_GROUP_ANALYSIS = "analysis"


def paragraph_comment_label(paragraph_index: int) -> str:
    return f"Paragraph {int(paragraph_index) + 1}"


def change_region_placeholder_body(page_index: int) -> str:
    return f"Changed area · Page {int(page_index) + 1}"


def plan_comment_list_label(page_index: int, kind: str) -> str:
    if kind == "pin":
        label = "pin"
    elif kind == "change_region":
        label = "area"
    else:
        label = "cloud"
    return f"Page {int(page_index) + 1} · {label}"


def sorted_comment_rows(comments: dict[int, str]) -> list[tuple[int, str]]:
    """Non-empty comments sorted by paragraph index (0-based)."""
    rows = [(int(k), (v or "").strip()) for k, v in comments.items()]
    return sorted((pi, body) for pi, body in rows if body)


def user_comment_list_key(paragraph_index: int) -> str:
    return f"ki_comment_{int(paragraph_index)}"


def impact_list_key(paragraph_index: int, prompt_id: str) -> str:
    return f"ki_impact_{int(paragraph_index)}_{str(prompt_id).strip()}"


def analyse_list_key(paragraph_index: int, check_id: str) -> str:
    return f"ki_analyse_{int(paragraph_index)}_{str(check_id).strip()}"


def analyse_group_key(check_id: str) -> str:
    return f"analyse:{str(check_id).strip()}"


def impact_group_key(prompt_id: str) -> str:
    return f"impact:{str(prompt_id).strip()}"


def truncate_preview(text: str, *, max_len: int = 220) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1] + "…"


@dataclass(frozen=True)
class CommentThreadItem:
    kind: Literal["user", "impact", "analyse"]
    paragraph_index: int
    title: str
    preview: str
    list_key: str
    version_line: str = ""
    prompt_id: str = ""
    check_id: str = ""
    symbol: str = ""
    symbol_color: str = ""
    symbol_is_icon: bool = False


def analysis_group_key_for_item(item: CommentThreadItem) -> str:
    if item.kind == "analyse":
        return analyse_group_key(item.check_id)
    if item.kind == "impact":
        return impact_group_key(item.prompt_id)
    return KI_COMMENTS_GROUP_USER


def sort_thread_items(items: list[CommentThreadItem]) -> list[CommentThreadItem]:
    """Sort by paragraph index; user notes before impact/analyse cards on the same paragraph."""
    kind_rank = {"user": 0, "impact": 1, "analyse": 2}
    return sorted(
        items,
        key=lambda it: (int(it.paragraph_index), kind_rank.get(it.kind, 2), it.list_key),
    )


def partition_thread_items(
    items: list[CommentThreadItem],
) -> tuple[list[CommentThreadItem], list[CommentThreadItem]]:
    """Split thread items into user comments vs analysis (impact + analyse)."""
    user = [it for it in items if it.kind == "user"]
    analysis = [it for it in items if it.kind in ("impact", "analyse")]
    return user, analysis


def version_line_from_run_context(ctx: dict[str, Any] | None) -> str:
    """Format baseline → candidate labels stored at Impact run time."""
    if not isinstance(ctx, dict):
        return ""
    baseline = str(ctx.get("baseline_label") or "").strip() or "Current draft"
    candidate = str(ctx.get("candidate_label") or "").strip() or "AI proposal (unsaved)"
    return f"{baseline} → {candidate}"
