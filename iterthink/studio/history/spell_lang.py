"""Map document text to a pyspellchecker language stem (auto mode)."""

from __future__ import annotations

import re
from typing import Final

# Stems matching pyspellchecker ``resources/*.json.gz``.
SUPPORTED_SPELL_LANGS: Final[tuple[str, ...]] = (
    "ar",
    "de",
    "en",
    "es",
    "eu",
    "fa",
    "fr",
    "it",
    "lv",
    "nl",
    "pt",
    "ru",
)

_SUPPORTED_SET: Final[frozenset[str]] = frozenset(SUPPORTED_SPELL_LANGS)

# langdetect ISO-ish codes → closest pyspellchecker stem (only keys we might see).
_LANGDETECT_TO_SPELL: Final[dict[str, str]] = {
    "so": "en",  # Somali → no dict
    "no": "en",
    "nb": "en",
    "nn": "en",
    "sv": "en",
    "da": "en",
    "fi": "en",
    "is": "en",
    "pl": "en",
    "cs": "en",
    "sk": "en",
    "hu": "en",
    "ro": "en",
    "bg": "en",
    "uk": "ru",
    "sr": "en",
    "hr": "en",
    "sl": "en",
    "et": "en",
    "lt": "lv",
    "tr": "en",
    "el": "en",
    "he": "en",
    "hi": "en",
    "ja": "en",
    "ko": "en",
    "zh-cn": "en",
    "zh-tw": "en",
    "th": "en",
    "vi": "en",
    "id": "en",
    "ms": "en",
    "tl": "en",
    "sw": "en",
    "cy": "en",
    "ga": "en",
    "ca": "es",
    "gl": "es",
    "af": "nl",
    "sq": "en",
    "mk": "en",
    "bs": "en",
}

_SAMPLE_MAX: Final[int] = 4096
_MIN_CHARS: Final[int] = 48
_MIN_PROB: Final[float] = 0.18

_fence_re = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_link_re = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_md_noise_re = re.compile(r"[#*_~`>|]+")


def _scrub_for_detection(raw: str) -> str:
    s = raw.strip()
    s = _fence_re.sub(" ", s)
    s = _link_re.sub(r"\1", s)
    s = _md_noise_re.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:_SAMPLE_MAX]


def _map_detected_code(code: str) -> str:
    low = (code or "").strip().lower()
    if "-" in low:
        low = low.split("-", 1)[0]
    if low in _SUPPORTED_SET:
        return low
    return _LANGDETECT_TO_SPELL.get(low, "en")


def detect_spell_language(text: str, *, fallback: str = "en") -> str:
    """Return a pyspellchecker stem: ``langdetect`` on a bounded sample, else ``fallback``."""
    fb = fallback if fallback in _SUPPORTED_SET else "en"
    sample = _scrub_for_detection(text or "")
    if len(sample) < _MIN_CHARS:
        return fb
    try:
        from langdetect import DetectorFactory, detect_langs
    except Exception:
        return fb

    DetectorFactory.seed = 0
    try:
        langs = detect_langs(sample)
    except Exception:
        return fb
    if not langs:
        return fb
    best = langs[0]
    prob = float(getattr(best, "prob", 0.0))
    code = str(getattr(best, "lang", "") or "")
    if prob < _MIN_PROB:
        return fb
    mapped = _map_detected_code(code)
    return mapped if mapped in _SUPPORTED_SET else fb
