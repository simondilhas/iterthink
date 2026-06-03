"""Tests for workspace RAG indexer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from unittest.mock import patch

import pytest

from iterthink.persistence import content_repo, store_db
from iterthink.services.rag.workspace_indexer import index_document_path


def _mock_embed_sync_vectors(conn: object, doc_key: str, inputs: list[str]) -> list[list[float]]:
    import numpy as np

    from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
    from iterthink.compare.paragraph_semantics import text_hash
    from iterthink.persistence import store_db

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


def test_index_document_skips_unchanged(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "a.md"
    md.write_text("# Hi\n\nBody text.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    async def _run() -> None:
        with session_scope() as s, patch(
            "iterthink.services.rag.workspace_indexer.embed_texts_cached",
            side_effect=_mock_embed,
        ):
            content_repo.persist_version_snapshot(s, md.resolve(), md.read_text(), "manual")
            s.commit()
            first = await index_document_path(
                s, conn, md, enrichment_mode="skip", latest_version_only=True
            )
            second = await index_document_path(
                s, conn, md, enrichment_mode="skip", latest_version_only=True
            )
        assert first is True
        assert second is False
        row = conn.execute("SELECT COUNT(*) FROM rag_child_chunk").fetchone()
        assert row is not None and int(row[0]) >= 1

    asyncio.run(_run())


def test_index_document_reindexes_on_change(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "b.md"
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
            content_repo.persist_version_snapshot(s, md.resolve(), "# Hi\n\nVersion one.", "manual")
            s.commit()
            await index_document_path(s, conn, md, enrichment_mode="skip", latest_version_only=True)
            content_repo.persist_version_snapshot(s, md.resolve(), "# Hi\n\nVersion two.", "manual")
            s.commit()
            again = await index_document_path(
                s, conn, md, enrichment_mode="skip", latest_version_only=True
            )
        assert again is True
        texts = [r[0] for r in conn.execute("SELECT raw_text FROM rag_child_chunk").fetchall()]
        assert any("Version two" in t for t in texts)

    asyncio.run(_run())
