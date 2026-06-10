"""Normalize import document-function classification settings."""

from __future__ import annotations

_VALID_TIERS = frozenset({"local", "company", "cloud"})
_DEFAULT_TIER = "local"


def normalize_import_classification_tier(raw: str | None) -> str:
    s = (raw or _DEFAULT_TIER).strip().lower()
    return s if s in _VALID_TIERS else _DEFAULT_TIER
