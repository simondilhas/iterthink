"""RAG enrichment LLM chat call shape."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from iterthink.services.rag.enrichment import enrich_child


def test_enrich_child_calls_chat_with_keywords() -> None:
    llm = AsyncMock()
    llm.chat.return_value = {"message": {"content": '{"summary": "Hi", "questions": ["a", "b", "c"]}'}}

    async def _run() -> None:
        summary, questions = await enrich_child(
            raw="Body text here.",
            header="Intro",
            doc_title="Doc",
            llm=llm,
            model="llama3.2",
        )
        llm.chat.assert_awaited_once()
        _args, kwargs = llm.chat.await_args
        assert _args == ()
        assert kwargs["model"] == "llama3.2"
        assert kwargs["messages"][0]["role"] == "system"
        assert kwargs["stream"] is False
        assert summary == "Hi"
        assert questions == ("a", "b", "c")

    asyncio.run(_run())
