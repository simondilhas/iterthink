"""Unit tests for compose selection markdown helpers."""

from __future__ import annotations

from iterthink.studio.compose_selection_markdown import (
    apply_bold_wrap,
    apply_bullet_block,
    apply_checklist_block,
    apply_indent_block,
    apply_italic_wrap,
    apply_numbered_block,
    apply_outdent_block,
    expand_selection_to_line_bounds,
)


def test_expand_selection_to_line_bounds() -> None:
    t = "aa\nbb cc\ndd"
    assert expand_selection_to_line_bounds(t, 4, 6) == (3, 9)  # through newline after "bb cc"
    assert expand_selection_to_line_bounds(t, 0, 2) == (0, 3)


def test_apply_bold_wrap() -> None:
    assert apply_bold_wrap("hello world", 6, 11) == ("hello **world**", 6, 15)
    u = apply_bold_wrap("x **ab** y", 2, 8)
    assert u is not None
    new, s0, s1 = u
    assert new == "x ab y" and (s0, s1) == (2, 4)


def test_apply_italic_wrap() -> None:
    assert apply_italic_wrap("a bc d", 2, 4) == ("a *bc* d", 2, 6)
    u = apply_italic_wrap("a *x* b", 2, 5)
    assert u is not None and u[0] == "a x b"


def test_apply_bullet_block() -> None:
    t = "one\ntwo\n"
    u = apply_bullet_block(t, 0, 7)
    assert u is not None
    new, s0, s1 = u
    assert "- one" in new and "- two" in new
    assert s0 == 0 and s1 == len(new)


def test_apply_numbered_block() -> None:
    t = "a\nb"
    u = apply_numbered_block(t, 0, 3)
    assert u is not None
    new, _, _ = u
    assert "1. a" in new and "2. b" in new


def test_apply_checklist_block() -> None:
    t = "one\ntwo\n"
    u = apply_checklist_block(t, 0, 7)
    assert u is not None
    new, _, _ = u
    assert "- [ ] one" in new and "- [ ] two" in new
    u2 = apply_checklist_block(new, 0, len(new) - 1)
    assert u2 is not None and u2[0] == new  # already tasks: unchanged


def test_apply_checklist_from_bullet() -> None:
    t = "- a\n* b\n"
    u = apply_checklist_block(t, 0, 8)
    assert u is not None
    assert "- [ ] a" in u[0] and "- [ ] b" in u[0]


def test_apply_indent_outdent() -> None:
    t = "a\n  b"
    u = apply_indent_block(t, 0, 5)
    assert u is not None and u[0] == "  a\n    b"
    u2 = apply_outdent_block(u[0], 0, len(u[0]))
    assert u2 is not None and u2[0] == "a\n  b"
