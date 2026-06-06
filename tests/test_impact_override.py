"""Tests for Impact paragraph override persistence and runner skip behavior."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from iterthink.db.session import session_scope
from iterthink.impact_checks import ImpactCheck
from iterthink.persistence import impact_annotations as impact_ann
from iterthink.persistence import store_db
from iterthink.services import impact_analysis_runner


def _legacy_check() -> ImpactCheck:
    return ImpactCheck(
        id="legacy_unit_check",
        label="Legacy unit",
        system_prompt="Return JSON with status stable|changed|risk.",
        user_template="Text:\n{text}\n\nContext:\n{context}",
    )


@pytest.mark.usefixtures("ephemeral_store")
def test_set_override_preserves_model_status_in_snapshot() -> None:
    with session_scope() as s:
        impact_ann.upsert_model_result(
            s,
            content_version_id=2,
            paragraph_index=0,
            prompt_id="norm_compliance",
            status="warning",
            comment="Model says fix refs",
            details={"findings": []},
        )
        impact_ann.set_override(
            s,
            content_version_id=2,
            paragraph_index=0,
            prompt_id="norm_compliance",
            status="ok",
            override_comment="Human accepts",
        )
        s.commit()
        row = impact_ann.list_for_version(
            s, content_version_id=2, prompt_id="norm_compliance"
        )[0]
        snap = impact_ann.snapshot_row_ui(row)
        assert snap["model_status"] == "warning"
        assert snap["model_comment"] == "Model says fix refs"
        assert snap["effective_comment"] == "Human accepts"
        assert snap["status"] == "ok"


@pytest.mark.usefixtures("ephemeral_store")
def test_set_override_updates_status_and_comment() -> None:
    with session_scope() as s:
        impact_ann.upsert_model_result(
            s,
            content_version_id=1,
            paragraph_index=0,
            prompt_id="legacy_unit_check",
            status="risk",
            comment="Model says risk",
            details={"explanation": "x"},
        )
        impact_ann.set_override(
            s,
            content_version_id=1,
            paragraph_index=0,
            prompt_id="legacy_unit_check",
            status="stable",
            override_comment="Human says stable",
        )
        s.commit()
        rows = impact_ann.list_for_version(
            s, content_version_id=1, prompt_id="legacy_unit_check"
        )
        row = rows[0]
        assert row.status == "stable"
        assert row.overridden is True
        assert row.override_comment == "Human says stable"
        snap = impact_ann.snapshot_row_ui(row)
        assert snap["effective_comment"] == "Human says stable"
        assert snap["overridden"] is True


@pytest.mark.usefixtures("ephemeral_store")
def test_clear_override_clears_flag() -> None:
    with session_scope() as s:
        impact_ann.upsert_model_result(
            s,
            content_version_id=1,
            paragraph_index=1,
            prompt_id="legacy_unit_check",
            status="changed",
            comment="Model",
        )
        impact_ann.set_override(
            s,
            content_version_id=1,
            paragraph_index=1,
            prompt_id="legacy_unit_check",
            status="stable",
            override_comment="Human",
        )
        impact_ann.clear_override(
            s,
            content_version_id=1,
            paragraph_index=1,
            prompt_id="legacy_unit_check",
        )
        s.commit()
        row = impact_ann.list_for_version(
            s, content_version_id=1, prompt_id="legacy_unit_check"
        )[1]
        assert row.overridden is False
        assert row.override_comment is None
        assert row.status == "changed"


@pytest.mark.usefixtures("ephemeral_store")
def test_run_impact_analysis_skips_overridden_paragraph() -> None:
    conn = store_db.connect()
    store_db.init_schema(conn)
    check = _legacy_check()
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value={
            "message": {
                    "content": json.dumps(
                        {
                            "status": "changed",
                            "comment": "LLM result",
                            "explanation": "LLM explanation",
                        }
                    )
            }
        }
    )

    with session_scope() as s:
        impact_ann.upsert_model_result(
            s,
            content_version_id=42,
            paragraph_index=0,
            prompt_id=check.id,
            status="risk",
            comment="Kept override",
            details={"explanation": "prior"},
        )
        impact_ann.set_override(
            s,
            content_version_id=42,
            paragraph_index=0,
            prompt_id=check.id,
            status="stable",
            override_comment="Human kept",
        )
        s.commit()

    calls: list[int] = []

    async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
        calls.append(idx)
        if idx == 0 and payload is not None:
            assert payload.get("overridden") is True
            assert payload.get("comment") == "Human kept"

    ready = impact_analysis_runner.ImpactContextReady(
        labels={},
        ingest=impact_analysis_runner.impact_rag.IngestResult(files=()),
        query_cache_key="impact_q::test",
        doc_title="Test",
        project_label=None,
        top_k=3,
    )

    with (
        patch(
            "iterthink.services.impact_analysis_runner.impact_rag.embed_paragraph_for_retrieval",
            new_callable=AsyncMock,
            return_value=[1.0] + [0.0] * 767,
        ),
        patch(
            "iterthink.services.impact_analysis_runner.impact_rag.retrieve_context_by_document_ids",
            return_value="",
        ),
        patch(
            "iterthink.services.impact_analysis_runner.impact_override.retrieve_override_context",
            return_value="",
        ),
        patch(
            "iterthink.services.impact_analysis_runner.impact_override.ensure_override_embedding",
            new_callable=AsyncMock,
        ),
    ):
        results = asyncio.run(
            impact_analysis_runner.run_impact_analysis(
                llm,
                model="test-model",
                check=check,
                conn=conn,
                target_document_id=1,
                target_version_id=42,
                context_document_ids=[],
                paragraphs=["Overridden paragraph.", "Run LLM on this one."],
                on_progress=on_progress,
                context_ready=ready,
            )
        )

    assert llm.chat.await_count == 1
    assert results[0] is not None
    assert results[0].get("overridden") is True
    assert results[1] is not None
    assert results[1].get("status") == "changed"
    assert 0 in calls and 1 in calls
