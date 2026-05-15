"""Full-document spelling suggestions for Review SPELL_PREVIEW mode."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from iterthink.persistence import store_db

_spell_obj: Any = False  # False = not built for current path; None = unavailable; else SpellChecker
_cached_setting_path: str | None = None  # last cfg string used for _spell_obj (None = never built)


def reset_spellchecker_cache() -> None:
    """Drop cached checker so the next lookup rebuilds (e.g. after Settings save)."""
    global _spell_obj, _cached_setting_path
    _spell_obj = False
    _cached_setting_path = None


def _read_dictionary_path_setting() -> str:
    try:
        conn = store_db.connect()
        try:
            raw = store_db.settings_get(conn, store_db.SETTINGS_SPELLCHECK_DICTIONARY_PATH)
            return (raw or "").strip()
        finally:
            conn.close()
    except Exception:
        return ""


def _build_spellchecker_for_path(path: str) -> Any:
    try:
        from spellchecker import SpellChecker
    except Exception:
        return None
    try:
        if path:
            expanded = Path(path).expanduser()
            if expanded.is_file():
                s = SpellChecker(language=None, local_dictionary=str(expanded))
            else:
                s = SpellChecker()
        else:
            s = SpellChecker()
    except Exception:
        return None
    try:
        if "the" not in s or "and" not in s:
            return None
    except Exception:
        return None
    return s


def _get_en_spellchecker():
    """Return a cached English ``SpellChecker`` or ``None`` if import/data failed.

    ``SpellChecker()`` can succeed while bundled data is missing (e.g. some frozen layouts),
    leaving an empty frequency table. Then ``the`` is "unknown" — treat that as unavailable.

    When ``SETTINGS_SPELLCHECK_DICTIONARY_PATH`` is set to an existing file, that JSON (or
    ``.json.gz``) word-frequency file is loaded instead of the bundled English dictionary.
    """
    global _spell_obj, _cached_setting_path
    cfg = _read_dictionary_path_setting()
    if _spell_obj is not False and _cached_setting_path == cfg:
        return _spell_obj
    _cached_setting_path = cfg
    _spell_obj = _build_spellchecker_for_path(cfg)
    return _spell_obj


def spellchecker_available() -> bool:
    return _get_en_spellchecker() is not None


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


def suggest_spell_corrected_text(text: str) -> str:
    """Return ``text`` with unknown ``[A-Za-z']{2,}`` tokens replaced by spellchecker suggestions.

    If ``pyspellchecker`` is not installed or the English dictionary did not load, returns
    ``text`` unchanged.
    """
    spell = _get_en_spellchecker()
    if spell is None:
        return text

    out: list[str] = []
    last = 0
    for m in re.finditer(r"[A-Za-z']{2,}", text):
        out.append(text[last : m.start()])
        wraw = m.group()
        low = wraw.lower()
        # ``unknown`` matches library semantics (case-folded); clearer than ``low in spell``.
        if not spell.unknown([wraw]):
            out.append(wraw)
        else:
            out.append(_replacement_for_unknown(spell, wraw, low))
        last = m.end()
    out.append(text[last:])
    return "".join(out)
