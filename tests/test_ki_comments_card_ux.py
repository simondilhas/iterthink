"""Tests for KI Comments grouped cards and unchanged analyse filtering."""

from iterthink import checks as checks_mod
from iterthink.checks import Check, CheckSymbol, unchanged_paragraph_payload
from iterthink.studio.ki_comments import (
    CommentThreadItem,
    partition_thread_items,
    user_comment_list_key,
)


def _minimal_check() -> Check:
    return Check(
        id="unit_test_check",
        label="Unit test",
        accent="#5AB0FF",
        system_prompt="You return JSON.",
        user_template="Compare:\nOLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(
            CheckSymbol(symbol="~", label="No meaningful change", color="#9AA0A6"),
            CheckSymbol(symbol="!", label="Change", color="#FF0000"),
        ),
    )


def test_is_unchanged_paragraph_payload_detects_skip_message() -> None:
    check = _minimal_check()
    payload = unchanged_paragraph_payload(check)
    assert checks_mod.is_unchanged_paragraph_payload(check, payload) is True


def test_is_unchanged_paragraph_payload_false_for_real_result() -> None:
    check = _minimal_check()
    payload = {
        "impact_symbol": "!",
        "summary": "Meaningful wording change.",
        "recommendations": [{"action": "Revise tone."}],
    }
    assert checks_mod.is_unchanged_paragraph_payload(check, payload) is False


def test_partition_thread_items_splits_user_and_analysis() -> None:
    items = [
        CommentThreadItem(
            kind="user",
            paragraph_index=0,
            title="P1",
            preview="note",
            list_key=user_comment_list_key(0),
        ),
        CommentThreadItem(
            kind="analyse",
            paragraph_index=1,
            title="Readability",
            preview="issue",
            list_key="ki_analyse_1_readability",
            check_id="readability",
            symbol="↑",
            symbol_color="#3FBE6B",
        ),
        CommentThreadItem(
            kind="impact",
            paragraph_index=2,
            title="Norm",
            preview="risk",
            list_key="ki_impact_2_norm",
            prompt_id="norm",
            symbol="!",
            symbol_color="#E57373",
        ),
    ]
    user, analysis = partition_thread_items(items)
    assert len(user) == 1
    assert user[0].kind == "user"
    assert len(analysis) == 2
    assert {it.kind for it in analysis} == {"analyse", "impact"}


def test_partition_empty_lists() -> None:
    assert partition_thread_items([]) == ([], [])
