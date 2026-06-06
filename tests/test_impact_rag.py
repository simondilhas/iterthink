"""Tests for Impact RAG ingest outcomes and retrieval."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import blob_to_floats, text_hash
from iterthink.persistence import content_repo, store_db
from iterthink.services.rag import impact_rag
from iterthink.services.rag.workspace_indexer import index_document_path


def _mock_embed_sync_vectors(conn: object, doc_key: str, inputs: list[str]) -> list[list[float]]:
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


async def _mock_embed(conn: object, doc_key: str, inputs: list[str]) -> list[list[float]]:
    return _mock_embed_sync_vectors(conn, doc_key, inputs)


def test_preflight_ingest_message_no_indexed_files() -> None:
    ingest = impact_rag.IngestResult(
        files=(
            impact_rag.IngestFileOutcome(
                path=Path("/tmp/missing.md"),
                document_id=1,
                lineage_id="lid-1",
                outcome="missing",
            ),
        )
    )
    msg = impact_rag.preflight_ingest_message(ingest, object())
    assert msg is not None
    assert "index" in msg.lower()


@pytest.mark.usefixtures("ephemeral_store")
def test_index_then_retrieve_context_by_document_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    ctx_md = doc_root / "context.md"
    ctx_md.write_text(
        "# Context doc\n\n"
        "The facade U-value must not exceed 0.15 W/m²K per project specification.\n\n"
        "Secondary paragraph with enough detail for semantic retrieval testing.",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    db_path = tmp_path / "store" / "store.rag.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    async def _run() -> None:
        with patch(
            "iterthink.services.rag.workspace_indexer.embed_texts_cached",
            side_effect=_mock_embed,
        ):
            with session_scope() as session:
                anchor = content_repo.get_or_create_lineage(session, ctx_md.resolve())
                session.commit()
                doc_id = int(anchor.id)
                outcome = await index_document_path(
                    session, conn, ctx_md.resolve(), enrichment_mode="skip"
                )
                session.commit()
                assert outcome in ("updated", "unchanged")

                lids = impact_rag.lineage_ids_for_document_ids(session, [doc_id])
                assert lids
                assert impact_rag.lineage_ids_with_index(conn, lids)
                assert impact_rag.active_chunk_count(conn, lids) > 0

                labels = impact_rag.document_label_map(session, [doc_id])
                vec = await impact_rag.embed_paragraph_for_retrieval(
                    conn,
                    "The facade U-value requirement is 0.15 W/m²K.",
                    cache_key="impact_q::test",
                    doc_title="Target",
                )
                assert vec

                ctx = impact_rag.retrieve_context_by_document_ids(
                    vec,
                    conn,
                    [doc_id],
                    labels,
                    top_k=3,
                    strict_filter=False,
                )
                assert ctx
                assert "facade" in ctx.lower() or "U-value" in ctx
                assert "context.md" in ctx or "Context doc" in ctx

    asyncio.run(_run())
    conn.close()


def test_retrieve_context_respects_strict_filter() -> None:
    _SCRATCH = Path(__file__).resolve().parents[1] / ".pytest_store"
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    db_path = _SCRATCH / f"strict_{uuid.uuid4().hex}.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)
    lid = "lineage-strict-filter"
    try:
        emb = np.zeros(768, dtype=np.float32)
        emb[0] = 1.0
        store_db.embedding_cache_put(conn, "dk", "h1", "mid", emb)
        rid1 = int(
            conn.execute(
                "SELECT vec_rowid FROM paragraph_embedding_cache WHERE input_hash = ?",
                ("h1",),
            ).fetchone()[0]
        )
        short_text = "Short label"
        pid = store_db.rag_parent_insert(
            conn,
            lineage_id=lid,
            content_version_id=1,
            parent_index=0,
            doc_title="Doc",
            section_header="Sec",
            parent_text=short_text,
            content_sha="sha",
        )
        store_db.rag_child_insert(
            conn,
            parent_id=pid,
            lineage_id=lid,
            content_version_id=1,
            slot_index=0,
            raw_text=short_text,
            summary="",
            questions_json="[]",
            embed_text=short_text,
            overlap_text="",
            input_hash="h1",
            vec_rowid=rid1,
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
        strict_txt = impact_rag.retrieve_context_by_lineage_ids(
            q, conn, [lid], {lid: "doc.md"}, top_k=1, strict_filter=True
        )
        relaxed_txt = impact_rag.retrieve_context_by_lineage_ids(
            q, conn, [lid], {lid: "doc.md"}, top_k=1, strict_filter=False
        )
        assert strict_txt == ""
        assert "Short label" in relaxed_txt
    finally:
        conn.close()
        for p in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            p.unlink(missing_ok=True)
