"""Tests for markdown content-tree heading parser."""

from iterthink.studio.content_tree import (
    ContentHeading,
    find_next_match_index,
    parse_markdown_headings,
)


def test_parse_multiple_levels_and_offsets() -> None:
    text = "# Title\n\nBody\n\n## Section\n"
    got = parse_markdown_headings(text)
    assert got == [
        ContentHeading(level=1, title="Title", offset=0),
        ContentHeading(level=2, title="Section", offset=15),
    ]


def test_headings_inside_fenced_code_ignored() -> None:
    text = "```\n# Not a heading\n```\n\n# Real\n"
    got = parse_markdown_headings(text)
    assert got == [ContentHeading(level=1, title="Real", offset=25)]


def test_empty_document() -> None:
    assert parse_markdown_headings("") == []
    assert parse_markdown_headings("   \n\n  ") == []


def test_setext_not_listed() -> None:
    text = "Title\n=====\n\n## ATX\n"
    got = parse_markdown_headings(text)
    assert got == [ContentHeading(level=2, title="ATX", offset=13)]


def test_closing_hashes_stripped() -> None:
    text = "## Hello world ##\n"
    got = parse_markdown_headings(text)
    assert got == [ContentHeading(level=2, title="Hello world", offset=0)]


def test_find_next_match_wraps() -> None:
    buf = "foo bar foo"
    assert find_next_match_index(buf, "foo", 0) == 0
    assert find_next_match_index(buf, "foo", 1) == 8
    assert find_next_match_index(buf, "foo", 9) == 0
    assert find_next_match_index(buf, "foo", 3) == 8
    assert find_next_match_index(buf, "baz", 0) is None


def test_find_next_empty_needle() -> None:
    assert find_next_match_index("abc", "", 0) is None
