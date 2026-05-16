"""Full-document spelling suggestions for Review SPELL_PREVIEW mode."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from iterthink.persistence import store_db
from iterthink.persistence.spell_resources import spell_dictionaries_dir

from .spell_lang import SUPPORTED_SPELL_LANGS, detect_spell_language

# Cache: key ``("override", path)`` or ``("lang", lang, resolved_path_str)`` → SpellChecker or None
_spell_cache: dict[tuple[str, ...], Any] = {}
_spell_cache_negative: set[tuple[str, ...]] = set()

# Per-language sanity: two frequent words that must be known if loaded from that dict.
_LANG_PROBES: dict[str, tuple[str, str]] = {
    "ar": ("في", "من"),
    "de": ("der", "und"),
    "en": ("the", "and"),
    "es": ("de", "la"),
    "eu": ("eta", "izan"),
    "fa": ("که", "از"),
    "fr": ("le", "les"),
    "it": ("il", "di"),
    "lv": ("un", "ir"),
    "nl": ("de", "het"),
    "pt": ("o", "de"),
    "ru": ("и", "не"),
}

_MIN_DICT_SIZE = 80

# Letters / marks across scripts; optional internal apostrophe for Latin contractions.
_TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*", re.UNICODE)


def reset_spellchecker_cache() -> None:
    """Drop cached checkers (e.g. after Settings save)."""
    global _spell_cache, _spell_cache_negative
    _spell_cache = {}
    _spell_cache_negative = set()


def _normalize_manual_lang(raw: str) -> str:
    low = (raw or "").strip().lower()
    return low if low in SUPPORTED_SPELL_LANGS else "en"


def _read_spell_settings() -> tuple[str, str, str]:
    """Return ``(dictionary_path, language_mode, manual_lang)``."""
    path = ""
    mode = "auto"
    manual = "en"
    try:
        conn = store_db.connect()
        try:
            p = store_db.settings_get(conn, store_db.SETTINGS_SPELLCHECK_DICTIONARY_PATH)
            path = (p or "").strip()
            m = store_db.settings_get(conn, store_db.SETTINGS_SPELLCHECK_LANGUAGE_MODE)
            if (m or "").strip().lower() == "manual":
                mode = "manual"
            ml = store_db.settings_get(conn, store_db.SETTINGS_SPELLCHECK_LANGUAGE)
            manual = _normalize_manual_lang(ml or "en")
        finally:
            conn.close()
    except Exception:
        pass
    return path, mode, manual


def _read_dictionary_path_setting() -> str:
    """Backward-compatible: optional custom dictionary file path only."""
    return _read_spell_settings()[0]


def _try_load_spellchecker(path: Path, *, lang: str | None, is_override: bool) -> Any:
    """Construct a ``SpellChecker`` from ``path`` or return ``None``."""
    try:
        from spellchecker import SpellChecker
    except Exception:
        return None
    try:
        s = SpellChecker(language=None, local_dictionary=str(path))
    except Exception:
        return None
    try:
        min_size = 3 if is_override else _MIN_DICT_SIZE
        if len(s.word_frequency.dictionary) < min_size:
            return None
    except Exception:
        return None
    if is_override:
        return s
    stem = lang or ""
    pair = _LANG_PROBES.get(stem)
    if pair is None:
        return s
    try:
        a, b = pair
        if a not in s or b not in s:
            return None
    except Exception:
        return None
    return s


def _fallback_package_spellchecker(lang: str) -> Any:
    try:
        from spellchecker import SpellChecker
    except Exception:
        return None
    try:
        s = SpellChecker(language=lang)
    except Exception:
        return None
    try:
        if len(s.word_frequency.dictionary) < _MIN_DICT_SIZE:
            return None
    except Exception:
        return None
    return s


def _fallback_default_spellchecker() -> Any:
    try:
        from spellchecker import SpellChecker
    except Exception:
        return None
    try:
        s = SpellChecker()
    except Exception:
        return None
    try:
        pair = _LANG_PROBES["en"]
        if pair[0] not in s or pair[1] not in s:
            return None
        if len(s.word_frequency.dictionary) < _MIN_DICT_SIZE:
            return None
    except Exception:
        return None
    return s


def _resolve_lang_for_text(text: str, mode: str, manual_lang: str) -> str:
    if mode == "manual":
        return manual_lang
    return detect_spell_language(text, fallback=manual_lang)


def _dict_path_for_lang(lang: str) -> Path:
    return spell_dictionaries_dir() / f"{lang}.json.gz"


def _get_spellchecker_cached(cache_key: tuple[str, ...], builder: Any) -> Any:
    if cache_key in _spell_cache_negative:
        return None
    if cache_key in _spell_cache:
        return _spell_cache[cache_key]
    obj = builder()
    if obj is None:
        _spell_cache_negative.add(cache_key)
        return None
    _spell_cache[cache_key] = obj
    return obj


def _checker_for_override(path_str: str) -> Any:
    expanded = Path(path_str).expanduser()
    if not expanded.is_file():
        return None
    key = ("override", str(expanded.resolve()))

    def build() -> Any:
        return _try_load_spellchecker(expanded, lang=None, is_override=True)

    return _get_spellchecker_cached(key, build)


def _checker_for_lang_stem(lang: str) -> Any:
    lang = _normalize_manual_lang(lang)
    p = _dict_path_for_lang(lang)
    key = ("lang", lang, str(p.resolve()) if p.is_file() else f"missing:{lang}")

    def build() -> Any:
        if p.is_file():
            got = _try_load_spellchecker(p, lang=lang, is_override=False)
            if got is not None:
                return got
        return _fallback_package_spellchecker(lang)

    return _get_spellchecker_cached(key, build)


def _default_availability_checker() -> Any:
    """Checker used for ``spellchecker_available()`` when no override is set."""
    _, mode, manual = _read_spell_settings()
    lang = manual if mode == "manual" else "en"
    c = _checker_for_lang_stem(lang)
    if c is not None:
        return c
    return _fallback_default_spellchecker()


def spellchecker_available() -> bool:
    path, _, _ = _read_spell_settings()
    if path:
        return _checker_for_override(path) is not None
    return _default_availability_checker() is not None


def _checker_for_suggest(text: str) -> Any:
    path, mode, manual = _read_spell_settings()
    if path:
        return _checker_for_override(path)
    lang = _resolve_lang_for_text(text, mode, manual)
    return _checker_for_lang_stem(lang)


def suggest_spell_corrected_text(text: str) -> str:
    """Return ``text`` with unknown word tokens replaced by spellchecker suggestions.

    Uses optional custom dictionary path, else store ``spell_dictionaries/{lang}.json.gz``
    with **auto** language detection or **manual** language from settings.
    """
    spell = _checker_for_suggest(text)
    if spell is None:
        return text

    out: list[str] = []
    last = 0
    for m in _TOKEN_RE.finditer(text):
        out.append(text[last : m.start()])
        wraw = m.group()
        low = wraw.lower()
        if not spell.unknown([wraw]):
            out.append(wraw)
        else:
            out.append(_replacement_for_unknown(spell, wraw, low))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _format_candidate(wraw: str, c: str) -> str:
    if not c:
        return wraw
    if wraw.isupper():
        return c.upper()
    if wraw[:1].isupper():
        return (c[:1].upper() + c[1:]) if len(c) > 1 else c.upper()
    return c


def _replacement_for_unknown(spell: object, wraw: str, low: str) -> str:
    """Pick one replacement for a misspelled token (correction first, then candidates; caps like toolbar)."""
    corr = spell.correction(low)  # type: ignore[attr-defined]
    if corr:
        return _format_candidate(wraw, corr)
    cands_raw = list(spell.candidates(low) or ())[:8]  # type: ignore[attr-defined]
    for c in cands_raw:
        if not c:
            continue
        return _format_candidate(wraw, c)
    return wraw
