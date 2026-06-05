"""Tests for parent-section RAG context in impact retrieval."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from iterthink.compare.paragraph_semantics import blob_to_floats
from iterthink.persistence import store_db
from iterthink.services.rag import impact_rag

_REPO = Path(__file__).resolve().parents[1]
_SCRATCH = _REPO / ".pytest_store"


def test_retrieve_context_dedupes_parent_sections() -> None:
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    db_path = _SCRATCH / f"parent_ctx_{uuid.uuid4().hex}.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)
    lid = "lineage-parent-dedupe"
    try:
        emb = np.zeros(768, dtype=np.float32)
        emb[0] = 1.0
        store_db.embedding_cache_put(conn, "dk", "h1", "mid", emb)
        store_db.embedding_cache_put(conn, "dk", "h2", "mid", emb)
        rid1 = int(
            conn.execute(
                "SELECT vec_rowid FROM paragraph_embedding_cache WHERE input_hash = ?",
                ("h1",),
            ).fetchone()[0]
        )
        rid2 = int(
            conn.execute(
                "SELECT vec_rowid FROM paragraph_embedding_cache WHERE input_hash = ?",
                ("h2",),
            ).fetchone()[0]
        )
        parent_body = (
            "First paragraph in section with enough characters for context.\n\n"
            "Second paragraph in section with enough characters for context."
        )
        pid = store_db.rag_parent_insert(
            conn,
            lineage_id=lid,
            content_version_id=1,
            parent_index=0,
            doc_title="Doc",
            section_header="Sec",
            parent_text=parent_body,
            content_sha="sha",
        )
        for slot, ih, rid in ((0, "h1", rid1), (1, "h2", rid2)):
            store_db.rag_child_insert(
                conn,
                parent_id=pid,
                lineage_id=lid,
                content_version_id=1,
                slot_index=slot,
                raw_text=parent_body.split("\n\n")[slot],
                summary="",
                questions_json="[]",
                embed_text=parent_body.split("\n\n")[slot],
                overlap_text="",
                input_hash=ih,
                vec_rowid=rid,
                embed_model_id="mid",
            )
        store_db.rag_lineage_index_put(
            conn,
            lineage_id=lid,
            content_version_id=1,
            content_sha="sha",
            enrichment_mode="skip",
            project_slug="Proj",
        )
        conn.commit()

        q = blob_to_floats(
            conn.execute("SELECT embedding FROM paragraph_vec WHERE rowid = ?", (rid1,)).fetchone()[0]
        )
        txt = impact_rag.retrieve_context_by_lineage_ids(
            q, conn, [lid], {lid: "doc.md"}, top_k=3
        )
        assert txt.count("First paragraph in section") == 1
        assert "Second paragraph in section" in txt
    finally:
        conn.close()
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            p.unlink(missing_ok=True)
