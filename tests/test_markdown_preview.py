"""Tests for Focus / help markdown preview preprocessing."""

from __future__ import annotations

from iterthink.studio.markdown_preview import markdown_preview_with_task_checkboxes


def test_task_preview_preserves_leading_indent_in_body() -> None:
    src = "- [ ]    nested note\n"
    out = markdown_preview_with_task_checkboxes(src)
    assert "\u2610" in out
    assert "    nested" in out


def test_task_preview_inserts_space_when_body_touches_bracket() -> None:
    out = markdown_preview_with_task_checkboxes("- [ ]foo\n")
    assert "\u2610 foo" in out


def test_task_preview_preserves_gfm_table_markdown() -> None:
    src = "Intro.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nOutro."
    out = markdown_preview_with_task_checkboxes(src)
    assert "| A | B |" in out
    assert "| 1 | 2 |" in out
    assert "Intro." in out
    assert "Outro." in out
