"""Tests for markdown preview transforms."""

from __future__ import annotations

import pytest

from iterthink.studio.markdown_preview import markdown_preview_with_task_checkboxes


@pytest.mark.parametrize(
    ("src", "want_substr"),
    [
        ("- [ ] a", "\u2610 a"),
        ("- [x] b", "\u2611 b"),
        ("- [X] c", "\u2611 c"),
        ("* [ ] d", "\u2610 d"),
        ("1. [ ] e", "\u2610 e"),
        ("12. [x] f", "\u2611 f"),
        ("  - [ ] g", "  \u2610 g"),
    ],
)
def test_task_checkbox_replacement(src: str, want_substr: str) -> None:
    out = markdown_preview_with_task_checkboxes(src)
    assert want_substr in out


def test_non_task_unchanged() -> None:
    s = "- plain item\n\n[x] not a list line"
    assert markdown_preview_with_task_checkboxes(s) == s


def test_empty() -> None:
    assert markdown_preview_with_task_checkboxes("") == ""


def test_task_only_checkbox_no_list_marker() -> None:
    out = markdown_preview_with_task_checkboxes("- [ ] a\n- plain")
    assert out.startswith("\u2610 a")
    assert "- plain" in out
    assert not out.startswith("- **")
