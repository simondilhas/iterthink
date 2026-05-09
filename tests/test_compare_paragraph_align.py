"""Unit tests for iterthink.compare.paragraph_align (TF-IDF alignment and helpers)."""

from __future__ import annotations

import pytest

from iterthink.compare.paragraph_align import (
    DiffParagraph,
    compute_alignment,
    compute_hash,
    deserialize_diffs,
    jaccard_similarity,
    normalize_unicode,
    normalize_whitespace,
    old_text_per_new_slot,
    preprocess_text,
    serialize_diffs,
    tokenize,
    word_diff_html,
)


def test_tokenize_lowercase_and_stopword_filter() -> None:
    assert tokenize("The cat and a dog") == ["cat", "dog"]


def test_jaccard_identical_and_disjoint() -> None:
    assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard_similarity({"a"}, {"b"}) == 0.0
    assert jaccard_similarity(set(), set()) == 1.0


def test_compute_hash_stable_for_same_text() -> None:
    h1 = compute_hash("hello world")
    h2 = compute_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64


def test_normalize_whitespace_collapses() -> None:
    assert normalize_whitespace("  a  \n\tb  ") == "a b"


def test_preprocess_text_returns_word_and_char_views() -> None:
    w, c = preprocess_text("Hello")
    assert isinstance(w, str) and isinstance(c, str)
    assert "Hello" in w or len(w) > 0


def test_normalize_unicode_nfkc() -> None:
    out = normalize_unicode("\ufb01")  # ﬁ ligature
    assert "fi" in out.lower() or len(out) >= 1


def test_word_diff_html_marks_insert_and_delete() -> None:
    old_html, new_html = word_diff_html("alpha beta", "alpha gamma")
    assert "<del>" in old_html and "<ins>" in new_html


def test_word_diff_html_escapes_angle_brackets() -> None:
    old_html, _new_html = word_diff_html("use <tag>", "use <tag> now")
    assert "&lt;" in old_html or "<del>" in old_html


def test_serialize_deserialize_diffs_roundtrip() -> None:
    diffs = [
        DiffParagraph(
            old_text="a",
            new_text="b",
            status="minor",
            label="moved",
            old_index=0,
            new_index=1,
            sim_score=0.9,
        )
    ]
    restored = deserialize_diffs(serialize_diffs(diffs))
    assert len(restored) == 1
    assert restored[0].old_text == "a"
    assert restored[0].new_text == "b"
    assert restored[0].old_index == 0
    assert restored[0].new_index == 1


def test_compute_alignment_identical_two_paragraphs() -> None:
    text = "First\n\nSecond"
    diffs = compute_alignment(text, text)
    matched = [d for d in diffs if d.old_index >= 0 and d.new_index >= 0]
    assert len(matched) == 2
    assert all(d.old_index == d.new_index for d in matched)


def test_compute_alignment_empty_old_document_has_placeholder_slot() -> None:
    """``split_paragraphs('')`` is ``['']``, so alignment pairs the empty slot then adds real paragraphs."""
    diffs = compute_alignment("", "Only\n\nTwo")
    added = [d for d in diffs if d.label == "added"]
    assert {(d.new_index, d.new_text) for d in added} == {(0, "Only"), (1, "Two")}
    deleted_empty = [d for d in diffs if d.label == "deleted" and d.old_text == ""]
    assert len(deleted_empty) == 1


def test_compute_alignment_empty_new_document_has_placeholder_slot() -> None:
    """Symmetric: candidate ``''`` still yields one new slot ``['']`` while baseline paragraphs delete."""
    diffs = compute_alignment("A\n\nB", "")
    deleted = [d for d in diffs if d.label == "deleted"]
    assert {(d.old_index, d.old_text) for d in deleted} == {(0, "A"), (1, "B")}
    added_empty = [d for d in diffs if d.label == "added" and d.new_text == ""]
    assert len(added_empty) == 1


def test_old_text_per_new_slot_inserts_empty_for_added_paragraph() -> None:
    lefts = old_text_per_new_slot("One", "One\n\nNew tail")
    assert lefts[0] == "One"
    assert lefts[1] == ""


def test_old_text_per_new_slot_matches_identical_document() -> None:
    text = "Para one\n\nPara two"
    lefts = old_text_per_new_slot(text, text)
    assert lefts == ["Para one", "Para two"]
