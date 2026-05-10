"""Tests for paragraph_semantics pure helpers (hash, cosine, blob)."""

from __future__ import annotations

import pytest

from iterthink.compare.paragraph_semantics import (
    blob_to_floats,
    cosine_sim,
    floats_to_blob,
    text_hash,
)


def test_text_hash_stable_utf8() -> None:
    h1 = text_hash("café")
    h2 = text_hash("café")
    assert h1 == h2
    assert len(h1) == 64
    assert text_hash("café") != text_hash("cafe")


def test_cosine_identical_unit_vector() -> None:
    v = [1.0, 0.0, 0.0]
    assert cosine_sim(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert cosine_sim(a, b) == pytest.approx(0.0)


def test_cosine_length_mismatch() -> None:
    assert cosine_sim([1.0, 0.0], [1.0]) == 0.0


def test_cosine_empty() -> None:
    assert cosine_sim([], []) == 0.0


def test_floats_blob_roundtrip() -> None:
    vals = [0.25, -1.5, 3.25]
    blob = floats_to_blob(vals)
    assert blob_to_floats(blob) == pytest.approx(vals)
