"""Tests for iterthink.persistence.store_db (SQLite + sqlite-vec)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import numpy as np
import pytest

from iterthink.persistence import store_db

_REPO = Path(__file__).resolve().parents[1]
_SCRATCH = _REPO / ".pytest_store"


@pytest.fixture
def store_conn():
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    db_path = _SCRATCH / f"store_{uuid.uuid4().hex}.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)
    yield conn
    conn.close()
    for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        p.unlink(missing_ok=True)


def test_settings_roundtrip(ephemeral_store: None) -> None:
    conn = store_db.connect()
    store_db.settings_set(conn, "k_test", "v1")
    assert store_db.settings_get(conn, "k_test") == "v1"
    store_db.settings_set(conn, "k_test", "v2")
    assert store_db.settings_get(conn, "k_test") == "v2"
    conn.close()


def test_manifest_roundtrip(store_conn) -> None:
    hashes = ["aaa", "bbb", "ccc"]
    store_db.manifest_put(store_conn, "/proj/a.md", "embed-model-x", 12345.67, hashes)
    row = store_db.manifest_get(store_conn, "/proj/a.md", "embed-model-x")
    assert row is not None
    assert row["file_path"] == "/proj/a.md"
    assert row["embed_model_id"] == "embed-model-x"
    assert float(row["file_mtime"]) == pytest.approx(12345.67)
    assert json.loads(row["chunk_hashes"]) == hashes


def test_embedding_cache_put_get_768(store_conn) -> None:
    vec = np.zeros(768, dtype=np.float32)
    vec[0] = 1.0
    vec[11] = -0.5
    doc = "doc:/x"
    h = "hash_one"
    model = "m-embed"
    store_db.embedding_cache_put(store_conn, doc, h, model, vec)
    blob = store_db.embedding_cache_get(store_conn, doc, h, model)
    assert blob is not None
    from iterthink.compare.paragraph_semantics import blob_to_floats

    out = blob_to_floats(blob)
    assert len(out) == 768
    assert out[0] == pytest.approx(1.0)
    assert out[11] == pytest.approx(-0.5)
