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


def test_change_region_placeholder_body() -> None:
    from iterthink.studio.ki_comments import change_region_placeholder_body

    assert change_region_placeholder_body(0) == "Changed area · Page 1"
    assert change_region_placeholder_body(2) == "Changed area · Page 3"


def test_plan_comment_list_label_change_region() -> None:
    from iterthink.studio.ki_comments import plan_comment_list_label

    assert plan_comment_list_label(0, "change_region") == "Page 1 · area"
    assert plan_comment_list_label(2, "pin") == "Page 3 · pin"
    assert plan_comment_list_label(1, "revision_cloud") == "Page 2 · cloud"
