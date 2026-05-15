"""Tests for iterthink.studio.history.spell_suggest."""

from __future__ import annotations

import pytest

from iterthink.studio.history import spell_suggest
from iterthink.studio.history.spell_suggest import spellchecker_available, suggest_spell_corrected_text


def test_spellchecker_available_matches_suggest_behavior() -> None:
    assert spellchecker_available() is True


def test_suggest_fixes_obvious_misspelling() -> None:
    got = suggest_spell_corrected_text("Please mispell check")
    assert "misspell" in got
    assert "mispell" not in got


def test_suggest_preserves_capitalization() -> None:
    got = suggest_spell_corrected_text("Mispell check")
    assert got.startswith("Misspell")


def test_suggest_leaves_known_words() -> None:
    s = "The quick brown fox jumps."
    assert suggest_spell_corrected_text(s) == s


def test_suggest_empty_and_non_alpha() -> None:
    assert suggest_spell_corrected_text("") == ""
    assert suggest_spell_corrected_text("123\n\n--") == "123\n\n--"


@pytest.mark.parametrize(
    "src,expect_sub",
    [
        ("teh end", "the"),
        ("recieve mail", "receive"),
    ],
)
def test_suggest_common_typos(src: str, expect_sub: str) -> None:
    got = suggest_spell_corrected_text(src)
    assert expect_sub in got


def test_reset_spellchecker_cache_forces_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = {"n": 0}
    real = spell_suggest._build_spellchecker_for_path

    def wrapped(path: str):
        builds["n"] += 1
        return real(path)

    monkeypatch.setattr(spell_suggest, "_build_spellchecker_for_path", wrapped)
    spell_suggest.reset_spellchecker_cache()
    assert spell_suggest.spellchecker_available() in (True, False)
    n1 = builds["n"]
    assert n1 >= 1
    _ = spell_suggest.spellchecker_available()
    assert builds["n"] == n1
    spell_suggest.reset_spellchecker_cache()
    _ = spell_suggest.spellchecker_available()
    assert builds["n"] == n1 + 1


def test_custom_local_dictionary_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "en_tiny.json"
    p.write_text('{"the":100,"and":100,"xyzzyuniqueword":1}', encoding="utf-8")
    spell_suggest.reset_spellchecker_cache()
    monkeypatch.setattr(spell_suggest, "_read_dictionary_path_setting", lambda: str(p))
    assert spell_suggest.spellchecker_available() is True
    assert spell_suggest.suggest_spell_corrected_text("xyzzyuniqueword") == "xyzzyuniqueword"
