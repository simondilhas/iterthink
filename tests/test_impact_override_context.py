"""Tests for Impact override embedding and prior-review context retrieval."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import text_hash
from iterthink.persistence import store_db
from iterthink.services.rag import impact_override
from iterthink.services.rag.context_format import format_override_context_block


def test_build_override_embed_text_includes_verdict_and_body() -> None:
    text = impact_override.build_override_embed_text(
        paragraph_text="Facade U-value 0.12.",
        paragraph_index=2,
        prompt_id="norm_compliance",
        status="warning",
        override_comment="Clarify reference to SIA 380/1.",
        doc_title="Spec",
    )
    assert "Human status: warning" in text
    assert "Human recommendation: Clarify reference" in text
    assert "Facade U-value 0.12." in text
    assert "Paragraph: 3" in text


def test_format_override_context_block() -> None:
    block = format_override_context_block(
        paragraph_index=1,
        status="ok",
        override_comment="Accept as written.",
        embed_text="Title: Doc\n---\nBody text.",
    )
    assert block is not None
    assert "[PRIOR REVIEW]" in block
    assert "paragraph=2" in block
    assert "human_status=ok" in block
    assert "Accept as written." in block


@pytest.mark.usefixtures("ephemeral_store")
def test_upsert_and_retrieve_override_context() -> None:
    conn = store_db.connect()
    store_db.init_schema(conn)
    assert store_db.RAG_SCHEMA_VERSION >= 4

    embed_text = impact_override.build_override_embed_text(
        paragraph_text="Alpha paragraph about thermal bridges.",
        paragraph_index=0,
        prompt_id="impact_consistency",
        status="warning",
        override_comment="Align with appendix B.",
        doc_title="Main",
    )
    vec = [1.0, 0.5] + [0.0] * 766
    store_db.embedding_cache_put(
        conn,
        impact_override.override_cache_key(10, "impact_consistency"),
        text_hash(embed_text),
        LOCAL_EMBEDDING_MODEL_ID,
        np.array(vec, dtype=np.float32),
    )
    vec_rowid = store_db.embedding_cache_vec_rowid(
        conn,
        impact_override.override_cache_key(10, "impact_consistency"),
        text_hash(embed_text),
        LOCAL_EMBEDDING_MODEL_ID,
    )
    assert vec_rowid is not None
    store_db.impact_override_context_upsert(
        conn,
        content_version_id=10,
        paragraph_index=0,
        prompt_id="impact_consistency",
        paragraph_text_hash=text_hash("Alpha paragraph about thermal bridges."),
        status="warning",
        override_comment="Align with appendix B.",
        embed_text=embed_text,
        vec_rowid=vec_rowid,
        embed_model_id=LOCAL_EMBEDDING_MODEL_ID,
    )

    other_embed = impact_override.build_override_embed_text(
        paragraph_text="Beta paragraph about windows.",
        paragraph_index=1,
        prompt_id="impact_consistency",
        status="error",
        override_comment="Fix glazing spec.",
        doc_title="Main",
    )
    vec2 = [0.9, 0.6] + [0.0] * 766
    store_db.embedding_cache_put(
        conn,
        impact_override.override_cache_key(10, "impact_consistency"),
        text_hash(other_embed),
        LOCAL_EMBEDDING_MODEL_ID,
        np.array(vec2, dtype=np.float32),
    )
    vec_rowid2 = store_db.embedding_cache_vec_rowid(
        conn,
        impact_override.override_cache_key(10, "impact_consistency"),
        text_hash(other_embed),
        LOCAL_EMBEDDING_MODEL_ID,
    )
    assert vec_rowid2 is not None
    store_db.impact_override_context_upsert(
        conn,
        content_version_id=10,
        paragraph_index=1,
        prompt_id="impact_consistency",
        paragraph_text_hash=text_hash("Beta paragraph about windows."),
        status="error",
        override_comment="Fix glazing spec.",
        embed_text=other_embed,
        vec_rowid=vec_rowid2,
        embed_model_id=LOCAL_EMBEDDING_MODEL_ID,
    )

    query_vec = [1.0, 0.55] + [0.0] * 766
    ctx = impact_override.retrieve_override_context(
        query_vec,
        conn,
        content_version_id=10,
        prompt_id="impact_consistency",
        exclude_paragraph_index=2,
        top_k=1,
    )
    assert "[PRIOR REVIEW]" in ctx
    assert "Align with appendix B." in ctx or "Fix glazing spec." in ctx


@pytest.mark.usefixtures("ephemeral_store")
def test_upsert_override_embedding_async() -> None:
    from unittest.mock import patch

    conn = store_db.connect()
    store_db.init_schema(conn)

    async def _fake_embed(c: object, key: str, inputs: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for inp in inputs:
            vec = [0.2, 0.8] + [0.0] * 766
            store_db.embedding_cache_put(
                c,  # type: ignore[arg-type]
                key,
                text_hash(inp),
                LOCAL_EMBEDDING_MODEL_ID,
                np.array(vec, dtype=np.float32),
            )
            out.append(vec)
        return out

    async def _run() -> None:
        with patch(
            "iterthink.services.rag.impact_override.embed_texts_cached",
            _fake_embed,
        ):
            await impact_override.upsert_override_embedding(
                conn,
                content_version_id=5,
                paragraph_index=0,
                prompt_id="risk_assessment",
                paragraph_text="Risk paragraph.",
                status="ok",
                override_comment="Accepted.",
                doc_title="Doc",
            )

    asyncio.run(_run())
    rows = store_db.impact_override_context_fetch_for_version(
        conn, content_version_id=5, prompt_id="risk_assessment"
    )
    assert len(rows) == 1
    assert rows[0][1] == "ok"
    assert rows[0][2] == "Accepted."
