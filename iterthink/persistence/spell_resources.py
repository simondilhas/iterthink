"""Copy pyspellchecker shipped dictionaries into the store for offline use."""

from __future__ import annotations

import shutil
from pathlib import Path

from iterthink import config


def spell_dictionaries_dir() -> Path:
    return config.STORE_DIR / "spell_dictionaries"


def _package_resources_dir() -> Path | None:
    try:
        import spellchecker as sc_pkg  # type: ignore[import-untyped]
    except Exception:
        return None
    base = Path(sc_pkg.__file__).resolve().parent
    res = base / "resources"
    return res if res.is_dir() else None


def ensure_spell_dictionaries() -> None:
    """Create ``spell_dictionaries`` under the store and copy each ``*.json.gz`` from pyspellchecker if missing."""
    src = _package_resources_dir()
    if src is None:
        return
    dest_root = spell_dictionaries_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    for gz in sorted(src.glob("*.json.gz")):
        target = dest_root / gz.name
        if target.is_file():
            continue
        try:
            shutil.copy2(gz, target)
        except OSError:
            continue
