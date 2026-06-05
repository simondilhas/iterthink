"""Tests for token usage extraction in llm_router."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from iterthink.ai.llm_router import (
    LlmChatBackend,
    TokenUsage,
    _anthropic_parse_usage,
    _gemini_parse_usage,
    _openai_parse_usage,
)


def test_openai_parse_usage() -> None:
    u = _openai_parse_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 20}})
    assert u is not None
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 20


def test_anthropic_parse_usage() -> None:
    u = _anthropic_parse_usage({"usage": {"input_tokens": 5, "output_tokens": 15}})
    assert u is not None
    assert u.prompt_tokens == 5
    assert u.completion_tokens == 15


def test_gemini_parse_usage() -> None:
    u = _gemini_parse_usage(
        {"usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 12}}
    )
    assert u is not None
    assert u.prompt_tokens == 8
    assert u.completion_tokens == 12


def test_company_nonstream_records_usage() -> None:
    recorded: list[tuple[str, TokenUsage | None]] = []

    def on_recorded() -> None:
        pass

    async def _run() -> None:
        backend = LlmChatBackend(
            ollama=MagicMock(),
            tier="company",
            cloud_vendor="openai",
            local_model="local",
            company_openai_model="gpt-4o-mini",
            company_openai_base_url="https://api.openai.com/v1",
            cloud_anthropic_model="",
            cloud_openai_model="",
            cloud_google_model="",
            secrets={"company_openai": "sk-test"},
            on_usage_recorded=on_recorded,
        )

        response_json = {
            "choices": [{"message": {"content": "Hi"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=response_json)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("iterthink.ai.llm_router.httpx.AsyncClient", return_value=mock_client),
            patch.object(
                backend,
                "_record_token_usage",
                side_effect=lambda m, u: recorded.append((m, u)),
            ),
        ):
            resp = await backend.chat(
                messages=[{"role": "user", "content": "hello"}],
                stream=False,
            )

        assert resp["message"]["content"] == "Hi"
        assert len(recorded) == 1
        model, usage = recorded[0]
        assert model == "gpt-4o-mini"
        assert usage is not None
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50

    asyncio.run(_run())


def test_openai_stream_records_usage_on_completion() -> None:
    recorded: list[TokenUsage | None] = []

    async def _run() -> None:
        backend = LlmChatBackend(
            ollama=MagicMock(),
            tier="company",
            cloud_vendor="openai",
            local_model="local",
            company_openai_model="gpt-4o-mini",
            company_openai_base_url="https://api.openai.com/v1",
            cloud_anthropic_model="",
            cloud_openai_model="",
            cloud_google_model="",
            secrets={"company_openai": "sk-test"},
        )

        chunks = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}],"usage":{"prompt_tokens":12,"completion_tokens":3}}',
            "data: [DONE]",
        ]

        class _FakeStream:
            def __init__(self) -> None:
                self._lines = iter(chunks)

            async def __aenter__(self) -> _FakeStream:
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self):
                for line in self._lines:
                    yield line

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=_FakeStream())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("iterthink.ai.llm_router.httpx.AsyncClient", return_value=mock_client),
            patch.object(backend, "_record_token_usage", side_effect=lambda _m, u: recorded.append(u)),
        ):
            stream = await backend.chat(
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
            )
            parts = []
            async for part in stream:
                parts.append(part["message"]["content"])

        assert parts == ["Hel", "lo"]
        assert len(recorded) == 1
        assert recorded[0] is not None
        assert recorded[0].prompt_tokens == 12
        assert recorded[0].completion_tokens == 3

    asyncio.run(_run())
