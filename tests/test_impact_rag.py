"""Tests for impact_rag.retrieve_context_for_paragraph."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from iterthink.compare.paragraph_semantics import blob_to_floats
from iterthink.persistence import store_db
from iterthink.services import impact_rag

_REPO = Path(__file__).resolve().parents[1]
_SCRATCH = _REPO / ".pytest_store"


def test_retrieve_context_empty_para_floats() -> None:
    conn = object()
    assert impact_rag.retrieve_context_for_paragraph([], {Path("a.md"): [("x", 1)]}, conn) == ""


def test_retrieve_context_empty_file_chunks() -> None:
    assert impact_rag.retrieve_context_for_paragraph([1.0, 0.0], {}, object()) == ""


def test_retrieve_context_ranks_by_cosine_similarity() -> None:
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    db_path = _SCRATCH / f"impact_{uuid.uuid4().hex}.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)
    try:
        emb_match = np.zeros(768, dtype=np.float32)
        emb_match[0] = 1.0
        emb_other = np.zeros(768, dtype=np.float32)
        emb_other[1] = 1.0
        store_db.embedding_cache_put(conn, "dk", "h1", "mid", emb_match)
        row1 = conn.execute(
            "SELECT vec_rowid FROM paragraph_embedding_cache WHERE doc_path = ? AND input_hash = ?",
            ("dk", "h1"),
        ).fetchone()
        assert row1 is not None
        rid1 = int(row1[0])

        store_db.embedding_cache_put(conn, "dk", "h2", "mid", emb_other)
        row2 = conn.execute(
            "SELECT vec_rowid FROM paragraph_embedding_cache WHERE doc_path = ? AND input_hash = ?",
            ("dk", "h2"),
        ).fetchone()
        assert row2 is not None
        rid2 = int(row2[0])

        query = blob_to_floats(
            conn.execute("SELECT embedding FROM paragraph_vec WHERE rowid = ?", (rid1,)).fetchone()[0]
        )
        md = Path("/tmp/context_rank.md")
        file_chunks = {
            md: [
                ("LOW_SIM chunk body", rid2),
                ("HIGH_SIM chunk body", rid1),
            ]
        }
        text = impact_rag.retrieve_context_for_paragraph(query, file_chunks, conn, top_k=1)
        assert "HIGH_SIM" in text
        assert "LOW_SIM" not in text
    finally:
        conn.close()
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            p.unlink(missing_ok=True)
