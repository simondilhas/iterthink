"""Tests for lazy tree helpers (list_visible_children, build_tree_from_md_paths, search)."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterthink import config
from iterthink.studio import tree


def _patch_store_outside_doc_tree(monkeypatch: pytest.MonkeyPatch, doc_root: Path) -> None:
    """Store/import dirs must not overlap doc_root for exclusion tests."""
    monkeypatch.setattr(config, "STORE_DIR", doc_root.parent / "iterthink_store_isolated")
    monkeypatch.setattr(config, "IMPORT_ASSETS_DIR", doc_root.parent / "iterthink_import_isolated")


def test_list_visible_children_sorting_not_guaranteed_but_splits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "proj"
    doc.mkdir()
    _patch_store_outside_doc_tree(monkeypatch, doc)
    (doc / "a.md").write_text("x", encoding="utf-8")
    (doc / "b.txt").write_text("y", encoding="utf-8")
    (doc / "sub").mkdir()
    dirs, files = tree.list_visible_children(doc)
    assert {p.name for p in dirs} == {"sub"}
    assert {p.name for p in files} == {"a.md"}


def test_list_visible_children_skips_iterthink_named_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "proj"
    doc.mkdir()
    _patch_store_outside_doc_tree(monkeypatch, doc)
    (doc / ".iterthink").mkdir()
    (doc / "visible").mkdir()
    dirs, files = tree.list_visible_children(doc)
    assert {p.name for p in dirs} == {"visible"}


def test_build_tree_from_md_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = tmp_path / "root"
    doc.mkdir()
    _patch_store_outside_doc_tree(monkeypatch, doc)
    p = doc / "d" / "n.md"
    p.parent.mkdir(parents=True)
    p.write_text("hi", encoding="utf-8")
    t = tree.build_tree_from_md_paths(doc, [p])
    assert "d" in t
    assert "_files" in t["d"]
    names = [fn for fn, _fp in t["d"]["_files"]]
    assert names == ["n.md"]


def test_build_search_md_tree_matches_path_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "root"
    doc.mkdir()
    _patch_store_outside_doc_tree(monkeypatch, doc)
    hit = doc / "alpha" / "x.md"
    hit.parent.mkdir(parents=True)
    hit.write_text("1", encoding="utf-8")
    miss = doc / "beta" / "y.md"
    miss.parent.mkdir(parents=True)
    miss.write_text("2", encoding="utf-8")
    t = tree.build_search_md_tree(doc, "alpha")
    assert "alpha" in t
    assert "beta" not in t


def test_build_search_md_tree_matches_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "root"
    doc.mkdir()
    _patch_store_outside_doc_tree(monkeypatch, doc)
    (doc / "notes.md").write_text("a", encoding="utf-8")
    t = tree.build_search_md_tree(doc, "note")
    assert "_files" in t
    assert any(fn == "notes.md" for fn, _fp in t["_files"])


def test_first_markdown_prefers_deeper_leftmost_name_az(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iterthink.studio.explorer import first_markdown_in_tree

    doc = tmp_path / "p"
    doc.mkdir()
    _patch_store_outside_doc_tree(monkeypatch, doc)
    (doc / "alpha").mkdir()
    (doc / "alpha" / "z.md").write_text("1", encoding="utf-8")
    (doc / "beta.md").write_text("2", encoding="utf-8")
    first = first_markdown_in_tree(doc, "name_az")
    assert first is not None
    assert first.name == "z.md"
