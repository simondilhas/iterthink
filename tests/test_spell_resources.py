"""Tests for iterthink.persistence.spell_resources."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterthink import config
from iterthink.persistence import spell_resources


def test_ensure_spell_dictionaries_copies_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "en.json.gz").write_bytes(b"x" * 20)
    (src / "de.json.gz").write_bytes(b"y" * 15)
    store = tmp_path / "store"
    monkeypatch.setattr(config, "STORE_DIR", store)

    def fake_pkg() -> Path:
        return src

    monkeypatch.setattr(spell_resources, "_package_resources_dir", fake_pkg)
    spell_resources.ensure_spell_dictionaries()
    dest = store / "spell_dictionaries" / "en.json.gz"
    assert dest.is_file()
    assert dest.read_bytes() == b"x" * 20
    assert (store / "spell_dictionaries" / "de.json.gz").read_bytes() == b"y" * 15

    # Second run: does not overwrite
    dest.write_bytes(b"changed")
    spell_resources.ensure_spell_dictionaries()
    assert dest.read_bytes() == b"changed"


def test_spell_dictionaries_dir_under_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "STORE_DIR", tmp_path / "s")
    assert spell_resources.spell_dictionaries_dir() == tmp_path / "s" / "spell_dictionaries"
