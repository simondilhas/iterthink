"""Tests for RAG index status."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from iterthink.persistence import content_repo, store_db
from iterthink.services.rag.index_status import compute_rag_index_status
from iterthink.services.rag.workspace_indexer import index_document_path


def test_compute_rag_index_status_active_and_historical(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    (doc_root / "a.md").write_text("# Hi\n\nBody.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    pid = store_db.rag_parent_insert(
        conn,
        lineage_id="lid-old",
        content_version_id=1,
        parent_index=0,
        doc_title="Hi",
        section_header="Hi",
        parent_text="Body.",
        content_sha="sha1",
    )
    store_db.rag_child_insert(
        conn,
        parent_id=pid,
        lineage_id="lid-old",
        content_version_id=1,
        slot_index=0,
        raw_text="Body.",
        summary="",
        questions_json="[]",
        embed_text="Body.",
        overlap_text="",
        input_hash="h1",
        vec_rowid=1,
        embed_model_id="mid",
    )
    pid2 = store_db.rag_parent_insert(
        conn,
        lineage_id="lid-old",
        content_version_id=2,
        parent_index=0,
        doc_title="Hi",
        section_header="Hi",
        parent_text="New body.",
        content_sha="sha2",
    )
    store_db.rag_child_insert(
        conn,
        parent_id=pid2,
        lineage_id="lid-old",
        content_version_id=2,
        slot_index=0,
        raw_text="New body.",
        summary="",
        questions_json="[]",
        embed_text="New body.",
        overlap_text="",
        input_hash="h2",
        vec_rowid=2,
        embed_model_id="mid",
    )
    store_db.rag_lineage_index_put(
        conn,
        lineage_id="lid-old",
        content_version_id=2,
        content_sha="sha2",
        enrichment_mode="skip",
    )
    conn.commit()

    from iterthink.db.session import session_scope

    with session_scope() as session:
        status = compute_rag_index_status(conn, session)
    assert status.indexed_documents == 1
    assert status.active_chunks == 1
    assert status.historical_chunks == 1
    assert status.total_documents == 1


async def _mock_embed(conn: object, doc_key: str, inputs: list[str]) -> list[list[float]]:
    import numpy as np

    from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
    from iterthink.compare.paragraph_semantics import text_hash

    out: list[list[float]] = []
    for i, inp in enumerate(inputs):
        vec = [float(i), 1.0] + [0.0] * 766
        h = text_hash(inp)
        store_db.embedding_cache_put(
            conn,  # type: ignore[arg-type]
            doc_key,
            h,
            LOCAL_EMBEDDING_MODEL_ID,
            np.array(vec, dtype=np.float32),
        )
        out.append(vec)
    return out


def test_latest_version_only_skips_no_snapshot(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "new.md"
    md.write_text("# Hi\n\nOnly on disk.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    async def _run() -> bool:
        with session_scope() as s, patch(
            "iterthink.services.rag.workspace_indexer.embed_texts_cached",
            side_effect=_mock_embed,
        ):
            return await index_document_path(
                s, conn, md, enrichment_mode="skip", latest_version_only=True
            )

    assert asyncio.run(_run()) is False
    assert conn.execute("SELECT COUNT(*) FROM rag_lineage_index").fetchone()[0] == 0


def test_new_version_retains_old_chunks(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "v.md"
    md.write_text("# Hi\n\nVersion one.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    async def _run() -> None:
        with session_scope() as s, patch(
            "iterthink.services.rag.workspace_indexer.embed_texts_cached",
            side_effect=_mock_embed,
        ):
            await index_document_path(s, conn, md, enrichment_mode="skip", latest_version_only=True)
            row = content_repo.get_artifact_lineage_by_path(s, md)
            assert row is not None
            lid = row.lineage_id
            content_repo.persist_version_snapshot(s, md.resolve(), "# Hi\n\nVersion two.", "manual")
            s.commit()
            await index_document_path(s, conn, md, enrichment_mode="skip", latest_version_only=True)
            count = conn.execute(
                "SELECT COUNT(*) FROM rag_child_chunk WHERE lineage_id = ?",
                (lid,),
            ).fetchone()[0]
            assert int(count) >= 2

    asyncio.run(_run())
