"""Document-function picker catalog (pragmatic-bim-data-contract v0.1.0)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent / "data" / "document_functions.json"

# Leaf concepts suitable for import picker (exclude top-level grouping nodes).
_PICKER_EXCLUDE_PARENTS_ONLY = frozenset(
    {
        "administrative",
        "financial",
        "legal_contractual",
        "regulatory_normative",
        "marketing_sales",
        "technical",
        "requirements_briefs",
    }
)


@lru_cache(maxsize=1)
def _catalog_rows() -> tuple[dict[str, Any], ...]:
    if not _DATA_PATH.is_file():
        return ()
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    rows = raw.get("functions") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return ()
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            out.append(row)
    return tuple(out)


def _label_for(row: dict[str, Any], *, locale: str = "en") -> str:
    if locale == "de":
        de = row.get("label_de")
        if isinstance(de, str) and de.strip():
            return de.strip()
    en = row.get("label_en")
    if isinstance(en, str) and en.strip():
        return en.strip()
    return str(row.get("id", ""))


def _parent_label(parent_id: str, by_id: dict[str, dict[str, Any]], *, locale: str) -> str:
    row = by_id.get(parent_id)
    if row is None:
        return parent_id
    return _label_for(row, locale=locale)


def is_valid_function_id(function_id: str) -> bool:
    fid = (function_id or "").strip()
    if not fid:
        return False
    return any(r.get("id") == fid for r in _catalog_rows())


def function_notation(function_id: str) -> str | None:
    fid = (function_id or "").strip()
    for row in _catalog_rows():
        if row.get("id") == fid:
            n = row.get("notation")
            return str(n).strip() if isinstance(n, str) and n.strip() else None
    return None


def function_label(function_id: str, *, locale: str = "en") -> str:
    fid = (function_id or "").strip()
    for row in _catalog_rows():
        if row.get("id") == fid:
            return _label_for(row, locale=locale)
    return fid or "Untitled"


def list_picker_options(*, locale: str = "en") -> list[tuple[str, str]]:
    """Return (function_id, grouped_display_label) for dropdown options."""
    rows = _catalog_rows()
    by_id = {str(r["id"]): r for r in rows if isinstance(r.get("id"), str)}
    options: list[tuple[str, str]] = []
    for row in rows:
        fid = str(row["id"])
        if fid in _PICKER_EXCLUDE_PARENTS_ONLY:
            continue
        parent = str(row.get("parent") or "")
        if parent == fid:
            continue
        leaf_label = _label_for(row, locale=locale)
        notation = row.get("notation")
        if isinstance(notation, str) and notation.strip():
            leaf_label = f"{notation} — {leaf_label}"
        if parent and parent != fid:
            group = _parent_label(parent, by_id, locale=locale)
            display = f"{group} — {leaf_label}"
        else:
            display = leaf_label
        options.append((fid, display))
    options.sort(key=lambda t: t[1].casefold())
    return options


def all_function_ids() -> tuple[str, ...]:
    return tuple(fid for fid, _ in list_picker_options())
