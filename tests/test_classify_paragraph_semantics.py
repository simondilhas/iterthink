"""Batch paragraph semantic classification (RAG observations)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import classify_paragraph_slots_batch, text_hash
from iterthink.db.session import session_scope
from iterthink.persistence import content_repo, store_db


def test_classify_paragraph_slots_batch_writes_observation(
    ephemeral_store: None, tmp_path: Path
) -> None:
    md = tmp_path / "sem.md"
    md.write_text("old\n\nsame", encoding="utf-8")
    with session_scope() as s:
        content_repo.get_or_create_lineage(s, md.resolve())
        s.commit()
        lineage_id = content_repo.lineage_id_for_resolved_path(s, md.resolve())
    assert lineage_id

    class _FakeChat:
        async def chat(self, **_kwargs):
            return {"message": {"content": "STABLE"}}

    fake_vec = [1.0, 0.0, 0.0]
    embed_mock = AsyncMock(return_value=[fake_vec])

    conn = store_db.connect()
    try:
        with patch(
            "iterthink.compare.paragraph_semantics.embed_texts_cached",
            embed_mock,
        ):
            results = asyncio.run(
                classify_paragraph_slots_batch(
                    conn,
                    _FakeChat(),
                    chat_model="test",
                    lineage_id=lineage_id,
                    doc_path=str(md.resolve()),
                    items=[(0, "old", "new text")],
                )
            )
        row = store_db.latest_observation(conn, lineage_id, 0, LOCAL_EMBEDDING_MODEL_ID)
    finally:
        conn.close()

    assert results == [(0, "STABLE")]
    assert text_hash("old") != text_hash("new text")
    assert row is not None
    assert row["status"] == "STABLE"
