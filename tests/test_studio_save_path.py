"""Tests for native save-dialog path normalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from iterthink.studio.util import normalize_save_file_path


def test_normalize_save_file_path_directory_pick(tmp_path: Path) -> None:
    folder = tmp_path / "exports"
    folder.mkdir()
    out = normalize_save_file_path(
        str(folder),
        default_file_name="note.docx",
        expected_suffix=".docx",
    )
    assert out == (folder / "note.docx").resolve()
    assert out.suffix == ".docx"


def test_normalize_save_file_path_adds_suffix(tmp_path: Path) -> None:
    out = normalize_save_file_path(
        str(tmp_path / "report"),
        default_file_name="note.docx",
        expected_suffix=".docx",
    )
    assert out.name == "report.docx"


def test_normalize_save_file_path_file_uri(tmp_path: Path) -> None:
    target = tmp_path / "out.docx"
    uri = target.as_uri()
    out = normalize_save_file_path(
        uri,
        default_file_name="ignored.docx",
        expected_suffix=".docx",
    )
    assert out == target.resolve()


def test_normalize_save_file_path_empty_raises() -> None:
    with pytest.raises(ValueError):
        normalize_save_file_path("", default_file_name="a.docx", expected_suffix=".docx")
