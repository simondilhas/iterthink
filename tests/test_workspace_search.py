"""Tests for workspace semantic search."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from iterthink import config
from iterthink.config import _bundled_defaults_dict
from iterthink.persistence import store_db
from iterthink.services.rag.workspace_search import parse_search_query, search_workspace
from iterthink.studio.search_results_ui import MarkdownStudioSearchResults


def test_bundled_rag_search_disabled() -> None:
    assert _bundled_defaults_dict()["rag_search_enabled"] is False


def test_rag_semantic_search_gated_when_feature_disabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "RAG_SEARCH_ENABLED", False)
    studio = MarkdownStudioSearchResults()
    studio.tree_search_field = MagicMock(value="notes")
    studio.tree_column = MagicMock()
    studio._search_gen = 0
    semantic_called: list[bool] = []
    rebuild_called: list[bool] = []

    async def _no_semantic(*_a: object, **_k: object) -> None:
        semantic_called.append(True)

    def _rebuild() -> None:
        rebuild_called.append(True)

    studio._debounced_semantic_search = _no_semantic  # type: ignore[method-assign]
    studio._rebuild_tree_ui = _rebuild  # type: ignore[method-assign]
    studio._show_search_results_panel = lambda _v: None  # type: ignore[method-assign]
    studio._on_tree_search_change()
    assert semantic_called == []
    assert rebuild_called == [True]


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


def test_search_workspace_filters_by_project_slug(tmp_path: Path) -> None:
    db_path = tmp_path / "search_scope.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)

    async def _run() -> None:
        try:
            emb = np.zeros(768, dtype=np.float32)
            emb[0] = 1.0
            store_db.embedding_cache_put(conn, "dk", "h", "mid", emb)
            rid = int(
                conn.execute(
                    "SELECT vec_rowid FROM paragraph_embedding_cache WHERE input_hash = ?",
                    ("h",),
                ).fetchone()[0]
            )

            def _seed_lineage(
                lid: str,
                slug: str | None,
                raw: str,
                version_id: int,
            ) -> None:
                pid = store_db.rag_parent_insert(
                    conn,
                    lineage_id=lid,
                    content_version_id=version_id,
                    parent_index=0,
                    doc_title="Doc",
                    section_header="Sec",
                    parent_text=raw,
                    content_sha=f"sha-{lid}",
                )
                store_db.rag_child_insert(
                    conn,
                    parent_id=pid,
                    lineage_id=lid,
                    content_version_id=version_id,
                    slot_index=0,
                    raw_text=raw,
                    summary="",
                    questions_json="[]",
                    embed_text=raw,
                    overlap_text="",
                    input_hash="h",
                    vec_rowid=rid,
                    embed_model_id="mid",
                )
                store_db.rag_lineage_index_put(
                    conn,
                    lineage_id=lid,
                    content_version_id=version_id,
                    content_sha=f"sha-{lid}",
                    enrichment_mode="skip",
                    project_slug=slug,
                )

            _seed_lineage("lid-a", "ProjA", "Alpha scoped paragraph with enough text.", 1)
            _seed_lineage("lid-b", "ProjB", "Beta scoped paragraph with enough text.", 2)
            conn.commit()

            session = MagicMock()
            with patch(
                "iterthink.services.rag.workspace_search._resolve_path_for_lineage",
                side_effect=lambda _s, lid: Path(f"/tmp/{lid}.md"),
            ):
                with patch(
                    "iterthink.services.rag.workspace_search._embed_query_sync",
                    return_value=emb.tolist(),
                ):
                    with patch("iterthink.config.RAG_RERANKER_ENABLED", False):
                        hits = await search_workspace(
                            "scoped",
                            conn,
                            session,
                            enrichment_mode="skip",
                            rerank=False,
                            project_slug="ProjA",
                        )
            assert len(hits) == 1
            assert "Alpha" in hits[0].raw_text
        finally:
            conn.close()

    asyncio.run(_run())
