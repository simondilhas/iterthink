"""Tests for KI Comments thread items and Impact run context helpers."""

from iterthink.persistence import impact_annotations as impact_ann
from iterthink.studio.ki_comments import (
    CommentThreadItem,
    impact_list_key,
    sort_thread_items,
    truncate_preview,
    user_comment_list_key,
    version_line_from_run_context,
)


def test_impact_list_key_format() -> None:
    assert impact_list_key(3, "norm_compliance") == "ki_impact_3_norm_compliance"


def test_user_comment_list_key_format() -> None:
    assert user_comment_list_key(5) == "ki_comment_5"


def test_sort_thread_items_user_before_impact_same_paragraph() -> None:
    items = sort_thread_items(
        [
            CommentThreadItem(
                kind="impact",
                paragraph_index=2,
                title="Norm compliance",
                preview="risk",
                list_key=impact_list_key(2, "norm_compliance"),
                prompt_id="norm_compliance",
            ),
            CommentThreadItem(
                kind="user",
                paragraph_index=2,
                title="Paragraph 3 · Note",
                preview="note",
                list_key=user_comment_list_key(2),
            ),
            CommentThreadItem(
                kind="user",
                paragraph_index=0,
                title="Paragraph 1 · Note",
                preview="a",
                list_key=user_comment_list_key(0),
            ),
        ]
    )
    assert [it.paragraph_index for it in items] == [0, 2, 2]
    assert items[1].kind == "user"
    assert items[2].kind == "impact"


def test_merge_run_context_preserves_findings() -> None:
    merged = impact_ann.merge_run_context(
        {"findings": [{"type": "error"}]},
        {
            "baseline_version_id": None,
            "candidate_version_id": 42,
            "baseline_label": "Current draft",
            "candidate_label": "AI proposal (unsaved)",
        },
    )
    assert merged is not None
    assert merged["findings"] == [{"type": "error"}]
    assert merged["_run_context"]["candidate_version_id"] == 42


def test_version_line_from_run_context_defaults() -> None:
    line = version_line_from_run_context(
        {
            "baseline_label": "",
            "candidate_label": "",
        }
    )
    assert line == "Current draft → AI proposal (unsaved)"


def test_truncate_preview() -> None:
    long = "x" * 300
    out = truncate_preview(long, max_len=220)
    assert out is not None
    assert len(out) == 220
    assert out.endswith("…")
