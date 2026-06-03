"""Tests for workspace semantic search."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from iterthink.persistence import store_db
from iterthink.services.rag.workspace_search import parse_search_query, search_workspace


def test_parse_search_query_filename_mode() -> None:
    q, mode = parse_search_query("/f notes")
    assert mode is True
    assert q == "notes"


def test_parse_search_query_semantic_mode() -> None:
    q, mode = parse_search_query("fire safety")
    assert mode is False
    assert q == "fire safety"


def test_search_workspace_ranks_by_embedding(tmp_path: Path) -> None:
    db_path = tmp_path / "search.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)

    async def _run() -> None:
        try:
            emb_a = np.zeros(768, dtype=np.float32)
            emb_a[0] = 1.0
            emb_b = np.zeros(768, dtype=np.float32)
            emb_b[1] = 1.0
            store_db.embedding_cache_put(conn, "dk", "ha", "mid", emb_a)
            store_db.embedding_cache_put(conn, "dk", "hb", "mid", emb_b)
            rid_a = int(
                conn.execute(
                    "SELECT vec_rowid FROM paragraph_embedding_cache WHERE input_hash = ?",
                    ("ha",),
                ).fetchone()[0]
            )
            rid_b = int(
                conn.execute(
                    "SELECT vec_rowid FROM paragraph_embedding_cache WHERE input_hash = ?",
                    ("hb",),
                ).fetchone()[0]
            )
            pid = store_db.rag_parent_insert(
                conn,
                lineage_id="lid1",
                content_version_id=1,
                parent_index=0,
                doc_title="Doc",
                section_header="Sec",
                parent_text="Alpha paragraph text.",
                content_sha="sha",
            )
            store_db.rag_child_insert(
                conn,
                parent_id=pid,
                lineage_id="lid1",
                content_version_id=1,
                slot_index=0,
                raw_text="Alpha paragraph text.",
                summary="",
                questions_json="[]",
                embed_text="Alpha paragraph text.",
                overlap_text="",
                input_hash="ha",
                vec_rowid=rid_a,
                embed_model_id="mid",
            )
            store_db.rag_child_insert(
                conn,
                parent_id=pid,
                lineage_id="lid1",
                content_version_id=1,
                slot_index=1,
                raw_text="Beta paragraph text.",
                summary="",
                questions_json="[]",
                embed_text="Beta paragraph text.",
                overlap_text="",
                input_hash="hb",
                vec_rowid=rid_b,
                embed_model_id="mid",
            )
            store_db.rag_lineage_index_put(
                conn,
                lineage_id="lid1",
                content_version_id=1,
                content_sha="sha",
                enrichment_mode="skip",
            )
            conn.commit()

            session = MagicMock()
            with patch(
                "iterthink.services.rag.workspace_search._resolve_path_for_lineage",
                return_value=Path("/tmp/doc.md"),
            ):
                with patch(
                    "iterthink.services.rag.workspace_search._embed_query_sync",
                    return_value=emb_a.tolist(),
                ):
                    with patch("iterthink.config.RAG_RERANKER_ENABLED", False):
                        hits = await search_workspace(
                            "alpha",
                            conn,
                            session,
                            enrichment_mode="skip",
                            rerank=False,
                        )
            assert hits
            assert "Alpha" in hits[0].raw_text
        finally:
            conn.close()

    asyncio.run(_run())
