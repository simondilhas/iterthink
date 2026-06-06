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
from iterthink.services.rag.workspace_search import SearchHit, parse_search_query, search_workspace
from iterthink.studio.search_results_ui import MarkdownStudioSearchResults
from iterthink.studio.main_workspace_tabs import MainWorkspaceTabsMixin


class _SearchUiStub(MarkdownStudioSearchResults):
    def __init__(self) -> None:
        self._init_search_results_ui()
        self._search_gen = 0
        self._main_tab_index = 1
        self.snacks: list[str] = []
        self.panel_visible: list[bool] = []
        self.debounce_calls: list[tuple[str, int]] = []

    def _snack(self, msg: str) -> None:
        self.snacks.append(msg)

    def _rag_search_enabled(self) -> bool:
        return True

    def _request_tab_switch(self, _tab_index: int) -> None:
        pass

    def _rebuild_tree_ui(self) -> None:
        pass

    def _make_tree_file_row(self, _name: str, _path: Path) -> object:
        return object()


class _ReviewSubtabStub(MainWorkspaceTabsMixin):
    _review_subtab_index = 0


def test_impact_subtab_label_plain() -> None:
    import flet as ft

    btn = _ReviewSubtabStub()._build_review_subtab_button("Impact", 1)
    assert isinstance(btn.content, ft.Text)
    assert btn.content.value == "Impact"
    assert "Coming Soon" not in (btn.content.value or "")
    assert "Comming Soon" not in (btn.content.value or "")


def test_bundled_rag_search_enabled() -> None:
    bundled = _bundled_defaults_dict()
    assert bundled["rag_search_enabled"] is True
    assert bundled["rag_system"] is True


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
    parsed = parse_search_query("/f notes")
    assert parsed.filename_mode is True
    assert parsed.query == "notes"
    assert parsed.project_slug is None


def test_parse_search_query_semantic_mode() -> None:
    parsed = parse_search_query("fire safety")
    assert parsed.filename_mode is False
    assert parsed.query == "fire safety"
    assert parsed.project_slug is None


def test_parse_search_query_project_mode() -> None:
    parsed = parse_search_query("/p AlphaProj fire safety")
    assert parsed.filename_mode is False
    assert parsed.project_slug == "AlphaProj"
    assert parsed.query == "fire safety"


def test_parse_search_query_project_mode_case_insensitive_prefix() -> None:
    parsed = parse_search_query("/P BetaProj notes")
    assert parsed.project_slug == "BetaProj"
    assert parsed.query == "notes"


def test_parse_search_query_project_without_query() -> None:
    parsed = parse_search_query("/p AlphaProj")
    assert parsed.project_slug == "AlphaProj"
    assert parsed.query == ""


def test_parse_search_query_project_prefix_only() -> None:
    parsed = parse_search_query("/p")
    assert parsed.project_slug is None
    assert parsed.query == ""


def test_on_tree_search_snacks_when_project_query_incomplete(monkeypatch) -> None:
    monkeypatch.setattr(config, "RAG_SEARCH_ENABLED", True)
    stub = _SearchUiStub()
    stub.tree_search_field = MagicMock(value="/p AlphaProj")
    stub.tree_column = MagicMock()
    stub.page = MagicMock()
    stub._show_search_results_panel = lambda _v: None  # type: ignore[method-assign]

    stub._on_tree_search_change()
    assert stub.snacks == ["Enter search terms after /p ProjectName"]
    stub.page.run_task.assert_not_called()


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


def test_search_workspace_parses_project_prefix(tmp_path: Path) -> None:
    db_path = tmp_path / "search_prefix.sqlite3"
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

            def _seed_lineage(lid: str, slug: str | None, raw: str, version_id: int) -> None:
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

            _seed_lineage("lid-a", "AlphaProj", "Alpha scoped paragraph with enough text.", 1)
            _seed_lineage("lid-b", "BetaProj", "Beta scoped paragraph with enough text.", 2)
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
                            "/p AlphaProj scoped",
                            conn,
                            session,
                            enrichment_mode="skip",
                            rerank=False,
                        )
            assert len(hits) == 1
            assert "Alpha" in hits[0].raw_text
        finally:
            conn.close()

    asyncio.run(_run())


def test_search_workspace_dedupes_parent_sections(tmp_path: Path) -> None:
    db_path = tmp_path / "search_parent_dedupe.sqlite3"
    conn = store_db.connect(db_path)
    store_db.init_schema(conn)

    async def _run() -> None:
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
                lineage_id="lid-parent",
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
                    lineage_id="lid-parent",
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
                lineage_id="lid-parent",
                content_version_id=1,
                content_sha="sha",
                enrichment_mode="skip",
                project_slug="Proj",
            )
            conn.commit()

            session = MagicMock()
            with patch(
                "iterthink.services.rag.workspace_search._resolve_path_for_lineage",
                return_value=Path("/tmp/doc.md"),
            ):
                with patch(
                    "iterthink.services.rag.workspace_search._embed_query_sync",
                    return_value=emb.tolist(),
                ):
                    with patch("iterthink.config.RAG_RERANKER_ENABLED", False):
                        hits = await search_workspace(
                            "paragraph section context",
                            conn,
                            session,
                            enrichment_mode="skip",
                            rerank=False,
                        )
            assert len(hits) == 1
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


def test_semantic_search_shows_loading_panel_immediately(monkeypatch) -> None:
    monkeypatch.setattr(config, "RAG_SEARCH_ENABLED", True)
    stub = _SearchUiStub()
    stub.tree_search_field = MagicMock(value="fire safety")
    stub.tree_column = MagicMock()
    stub.page = MagicMock()

    def _capture_panel(visible: bool) -> None:
        stub.panel_visible.append(visible)
        MarkdownStudioSearchResults._show_search_results_panel(stub, visible)

    stub._show_search_results_panel = _capture_panel  # type: ignore[method-assign]
    stub._on_tree_search_change()

    assert stub.panel_visible == [True]
    assert len(stub._search_results_list.controls) == 1
    stub.page.run_task.assert_called_once_with(
        stub._debounced_semantic_search, "fire safety", 1, None
    )


def test_show_search_results_panel_updates_mounted_controls(monkeypatch) -> None:
    monkeypatch.setattr(config, "RAG_SEARCH_ENABLED", True)
    stub = _SearchUiStub()
    stub._compose_writing_slot = MagicMock()
    stub._compose_writing_slot.visible = True
    stub._compose_centered_row = MagicMock()
    stub._compose_centered_row.visible = True
    stub._compose_reading_inner = MagicMock()
    stub._compose_tab_body_stack = MagicMock()
    stub.page = MagicMock()

    with patch("iterthink.studio.search_results_ui._ctrl_on_page", return_value=True):
        with patch.object(stub._search_results_host, "update") as host_update:
            with patch.object(stub._search_results_list, "update") as list_update:
                stub._show_search_results_panel(True)

    assert stub._search_results_host.visible is True
    assert stub._compose_centered_row.visible is False
    assert stub._compose_writing_slot.visible is False
    host_update.assert_called_once()
    list_update.assert_called_once()
    stub._compose_writing_slot.update.assert_called_once()
    stub._compose_reading_inner.update.assert_called_once()
    stub._compose_tab_body_stack.update.assert_called_once()
    stub.page.update.assert_called_once()


def test_run_semantic_search_async_renders_hit_cards(monkeypatch) -> None:
    monkeypatch.setattr(config, "RAG_SEARCH_ENABLED", True)
    stub = _SearchUiStub()
    stub._db = MagicMock()
    stub.tree_column = MagicMock()
    stub._search_gen = 1

    hit = SearchHit(
        lineage_id="lid1",
        resolved_path=Path("/tmp/doc.md"),
        doc_title="Doc",
        section_header="Sec",
        raw_text="Alpha paragraph text.",
        parent_text="Alpha paragraph text.",
        slot_index=0,
        score=0.9,
    )

    async def _fake_search(*_a: object, **_k: object) -> list[SearchHit]:
        return [hit]

    with patch(
        "iterthink.studio.search_results_ui.search_workspace",
        side_effect=_fake_search,
    ):
        with patch("iterthink.studio.search_results_ui.session_scope"):
            with patch.object(stub, "_rag_enrichment_mode", return_value="skip"):
                with patch.object(stub, "_rag_enrichment_tier", return_value="local"):
                    with patch.object(stub, "_rag_llm_bundle", return_value=(None, None)):
                        with patch.object(stub, "_rag_reranker_enabled", return_value=False):
                            with patch.object(stub, "_rag_latest_version_only", return_value=True):
                                with patch(
                                    "iterthink.studio.search_results_ui._ctrl_on_page",
                                    return_value=False,
                                ):
                                    asyncio.run(stub._run_semantic_search_async("alpha", 1))

    assert stub._search_hits == [hit]
    assert len(stub._search_results_list.controls) == 1
    assert stub._semantic_search_active is True
    assert stub._search_results_host.visible is True
