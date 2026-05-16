"""KI sidebar paragraph comment list helpers."""

from iterthink.studio.ki_comments import paragraph_comment_label, sorted_comment_rows


def test_paragraph_comment_label_is_one_based() -> None:
    assert paragraph_comment_label(0) == "Paragraph 1"
    assert paragraph_comment_label(4) == "Paragraph 5"


def test_sorted_comment_rows_orders_and_skips_empty() -> None:
    comments = {2: "third", 0: "first", 5: "   ", 1: "second"}
    assert sorted_comment_rows(comments) == [(0, "first"), (1, "second"), (2, "third")]


def test_sorted_comment_rows_empty() -> None:
    assert sorted_comment_rows({}) == []
    assert sorted_comment_rows({0: "", 1: "  "}) == []
