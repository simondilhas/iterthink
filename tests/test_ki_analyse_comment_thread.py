"""Tests for KI Comments thread items for Analyse checks."""

from iterthink.studio.ki_comments import (
    CommentThreadItem,
    analyse_list_key,
    impact_list_key,
    sort_thread_items,
    user_comment_list_key,
)


def test_analyse_list_key_format() -> None:
    assert analyse_list_key(8, "readability") == "ki_analyse_8_readability"


def test_sort_thread_items_user_impact_analyse_same_paragraph() -> None:
    items = sort_thread_items(
        [
            CommentThreadItem(
                kind="analyse",
                paragraph_index=2,
                title="Readability",
                preview="skipped",
                list_key=analyse_list_key(2, "readability"),
                check_id="readability",
            ),
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
    assert [it.paragraph_index for it in items] == [0, 2, 2, 2]
    assert items[1].kind == "user"
    assert items[2].kind == "impact"
    assert items[3].kind == "analyse"
