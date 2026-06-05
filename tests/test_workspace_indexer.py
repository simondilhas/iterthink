"""Tests for workspace RAG indexer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from unittest.mock import patch

import pytest

from sqlalchemy.exc import OperationalError

from iterthink.persistence import content_repo, store_db
from iterthink.services.rag.workspace_indexer import index_all_documents, index_document_path


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
        assert first == "updated"
        assert second == "unchanged"
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
        assert again == "updated"
        texts = [r[0] for r in conn.execute("SELECT raw_text FROM rag_child_chunk").fetchall()]
        assert any("Version two" in t for t in texts)

    asyncio.run(_run())


def test_index_all_documents_commits_per_file(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md1 = doc_root / "one.md"
    md2 = doc_root / "two.md"
    md1.write_text("# One\n\nFirst.", encoding="utf-8")
    md2.write_text("# Two\n\nSecond.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    commit_calls: list[int] = []

    async def _run() -> None:
        with session_scope() as s, patch(
            "iterthink.services.rag.workspace_indexer.embed_texts_cached",
            side_effect=_mock_embed,
        ), patch(
            "iterthink.services.rag.workspace_indexer.iter_workspace_markdown_paths",
            return_value=[md1.resolve(), md2.resolve()],
        ):
            original_commit = s.commit

            def tracked_commit() -> None:
                commit_calls.append(1)
                original_commit()

            s.commit = tracked_commit  # type: ignore[method-assign]
            result = await index_all_documents(s, conn, enrichment_mode="skip")
        assert result.scanned == 2
        assert len(commit_calls) >= 2

    asyncio.run(_run())


def test_settings_set_after_per_doc_index_commit(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "a.md"
    md.write_text("# Hi\n\nBody.", encoding="utf-8")
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
            await index_document_path(s, conn, md, enrichment_mode="skip", latest_version_only=True)
            s.commit()
            store_db.settings_set(conn, store_db.SETTINGS_RAG_ENRICHMENT_TIER, "local")

    asyncio.run(_run())
    assert store_db.settings_get(conn, store_db.SETTINGS_RAG_ENRICHMENT_TIER) == "local"


def test_settings_set_succeeds_after_pre_enrichment_commit(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "a.md"
    md.write_text("# Hi\n\nBody.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    async def _run() -> None:
        with session_scope() as s, patch(
            "iterthink.services.rag.workspace_indexer.embed_texts_cached",
            side_effect=_mock_embed,
        ), patch(
            "iterthink.services.rag.workspace_indexer.enrich_child",
            side_effect=lambda **_: ("summary", ["q1?", "q2?", "q3?"]),
        ):
            content_repo.persist_version_snapshot(s, md.resolve(), md.read_text(), "manual")
            await index_document_path(
                s, conn, md, enrichment_mode="local", llm=object(), llm_model="m"
            )
            store_db.settings_set(conn, store_db.SETTINGS_KI_TIER, "cloud")

    asyncio.run(_run())
    assert store_db.settings_get(conn, store_db.SETTINGS_KI_TIER) == "cloud"


def test_settings_set_blocked_while_uncommitted_entity_write(
    tmp_path: Path, ephemeral_store: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_root = tmp_path / "docs"
    doc_root.mkdir()
    md = doc_root / "a.md"
    md.write_text("# Hi\n\nBody.", encoding="utf-8")
    monkeypatch.setattr("iterthink.config.DOCUMENTS", doc_root)

    conn = store_db.connect()
    store_db.init_schema(conn)

    from iterthink.db.session import session_scope

    with session_scope() as s:
        content_repo.persist_version_snapshot(s, md.resolve(), md.read_text(), "manual")
        with pytest.raises(OperationalError):
            store_db.settings_set(conn, store_db.SETTINGS_RAG_ENRICHMENT_TIER, "company")
