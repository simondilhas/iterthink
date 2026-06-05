"""Ollama OCR readiness checks."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from iterthink.ai import ollama_models, ollama_ocr


def test_model_name_installed_tag_matching() -> None:
    installed = ["llava:latest", "llama3:8b"]
    assert ollama_models.model_name_installed(installed, "llava")
    assert not ollama_models.model_name_installed(installed, "moondream")
    assert ollama_models.model_name_installed(installed, "llava:latest")


def test_check_ollama_ocr_ready_unreachable() -> None:
    async def _run() -> None:
        ollama = AsyncMock()
        ollama.list.side_effect = ConnectionError("refused")
        ok, msg = await ollama_ocr.check_ollama_ocr_ready(ollama, "llava")
        assert not ok
        assert "reachable" in msg.lower()

    asyncio.run(_run())


def test_check_ollama_ocr_ready_missing_model() -> None:
    async def _run() -> None:
        ollama = AsyncMock()
        model = MagicMock()
        model.model = "llama3:8b"
        lr = MagicMock()
        lr.models = [model]
        ollama.list.return_value = lr
        ok, msg = await ollama_ocr.check_ollama_ocr_ready(ollama, "llava")
        assert not ok
        assert "not installed" in msg.lower()

    asyncio.run(_run())


def test_check_ollama_ocr_ready_ok() -> None:
    async def _run() -> None:
        ollama = AsyncMock()
        model = MagicMock()
        model.model = "llava:latest"
        lr = MagicMock()
        lr.models = [model]
        ollama.list.return_value = lr
        ok, msg = await ollama_ocr.check_ollama_ocr_ready(ollama, "llava")
        assert ok
        assert msg == "Ready"

    asyncio.run(_run())
