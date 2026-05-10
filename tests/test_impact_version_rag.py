"""Version-scoped Impact RAG: MAX(ver_id) per doc_id."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from iterthink.compare.paragraph_semantics import blob_to_floats
from iterthink.persistence import store_db
from iterthink.services.rag import impact_rag


_REPO = Path(__file__).resolve().parents[1]
_SCRATCH = _REPO / ".pytest_store"


def test_fetch_latest_rows_prefers_max_version_per_doc() -> None:
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    db_path = _SCRATCH / f"impact_ver_{uuid.uuid4().hex}.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)
    try:
        emb = np.zeros(768, dtype=np.float32)
        emb[0] = 1.0
        dk_v1 = impact_rag.doc_key_version(1, 10)
        dk_v2 = impact_rag.doc_key_version(1, 20)
        store_db.embedding_cache_put(conn, dk_v1, "ha", "mid", emb)
        rid1 = int(
            conn.execute(
                "SELECT vec_rowid FROM paragraph_embedding_cache WHERE doc_path=? AND input_hash=?",
                (dk_v1, "ha"),
            ).fetchone()[0]
        )
        store_db.embedding_cache_put(conn, dk_v2, "hb", "mid", emb)
        rid2 = int(
            conn.execute(
                "SELECT vec_rowid FROM paragraph_embedding_cache WHERE doc_path=? AND input_hash=?",
                (dk_v2, "hb"),
            ).fetchone()[0]
        )
        store_db.impact_version_chunk_insert_row(
            conn,
            doc_id=1,
            ver_id=10,
            chunk_index=0,
            input_hash="ha",
            vec_rowid=rid1,
            embed_model_id="mid",
            chunk_text="OLD paragraph with enough characters for norm context filter.",
            content_sha="s1",
        )
        store_db.impact_version_chunk_insert_row(
            conn,
            doc_id=1,
            ver_id=20,
            chunk_index=0,
            input_hash="hb",
            vec_rowid=rid2,
            embed_model_id="mid",
            chunk_text="NEW paragraph with enough characters for norm context filter.",
            content_sha="s2",
        )
        conn.commit()

        rows = store_db.impact_version_chunk_fetch_latest_rows(conn, [1])
        assert len(rows) == 1
        assert rows[0][1] == 20
        assert "NEW" in rows[0][4]
        assert rows[0][5] == "unknown"

        q = blob_to_floats(
            conn.execute("SELECT embedding FROM paragraph_vec WHERE rowid=?", (rid2,)).fetchone()[0]
        )
        labels = {1: "doc1.md"}
        txt = impact_rag.retrieve_context_by_document_ids(q, conn, [1], labels, top_k=1)
        assert "NEW" in txt
        assert "OLD" not in txt
        assert "chunk_index=0" in txt
        assert "paragraph=1" in txt
    finally:
        conn.close()
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            p.unlink(missing_ok=True)
