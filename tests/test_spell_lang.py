"""Tests for iterthink.studio.history.spell_lang."""

from __future__ import annotations

import pytest

from iterthink.studio.history import spell_lang


def test_detect_short_text_uses_fallback() -> None:
    assert spell_lang.detect_spell_language("hi", fallback="de") == "de"


def test_detect_german_sample() -> None:
    sample = (
        "Der schnelle braune Fuchs springt über den faulen Hund. "
        "Dies ist ein weiterer deutscher Satz für die Erkennung."
    )
    got = spell_lang.detect_spell_language(sample, fallback="en")
    assert got == "de"


def test_detect_english_sample() -> None:
    sample = (
        "The quick brown fox jumps over the lazy dog. "
        "This paragraph should be classified as English for spelling purposes."
    )
    assert spell_lang.detect_spell_language(sample, fallback="de") == "en"


def test_map_unsupported_langdetect_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLang:
        def __init__(self, lang: str, prob: float) -> None:
            self.lang = lang
            self.prob = prob

    monkeypatch.setattr(
        "langdetect.detect_langs",
        lambda _text: [FakeLang("no", 0.99)],
    )
    sample = "x" * 60 + " " + "y" * 60
    assert spell_lang.detect_spell_language(sample, fallback="fr") == "en"
