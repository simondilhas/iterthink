"""Ollama vision OCR for scanned imports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.ai.ollama_models import model_name_installed, vision_model_names
from iterthink.ai.ollama_util import chat_response_text, ollama_error_message
from iterthink.ocr_settings import DEFAULT_OLLAMA_OCR_MODEL

_OCR_PROMPT = (
    "Extract all visible text from this image. "
    "Output only the extracted text, preserving line breaks. No commentary."
)


def _sync_client() -> Any:
    from ollama import Client

    return Client(host=config.OLLAMA_HOST) if config.OLLAMA_HOST else Client()


def _chat_ocr(client: Any, image_path: Path, model: str) -> str:
    resp = client.chat(
        model=model,
        messages=[
            {
                "role": "user",
                "content": _OCR_PROMPT,
                "images": [str(image_path.resolve())],
            }
        ],
    )
    return chat_response_text(resp).strip()


def ocr_image_sync(image_path: Path, *, model: str | None = None) -> str:
    model_name = (model or config.OCR_MODEL or DEFAULT_OLLAMA_OCR_MODEL).strip()
    return _chat_ocr(_sync_client(), image_path, model_name)


async def ocr_image_async(ollama: Any, image_path: Path, *, model: str | None = None) -> str:
    model_name = (model or config.OCR_MODEL or DEFAULT_OLLAMA_OCR_MODEL).strip()
    resp = await ollama.chat(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": _OCR_PROMPT,
                "images": [str(image_path.resolve())],
            }
        ],
    )
    return chat_response_text(resp).strip()


async def _installed_model_names(ollama: Any) -> list[str]:
    lr = await ollama.list()
    return sorted({str(m.model) for m in lr.models if getattr(m, "model", None)})


async def check_ollama_ocr_ready(
    ollama: Any,
    model: str | None = None,
) -> tuple[bool, str]:
    model_name = (model or config.OCR_MODEL or DEFAULT_OLLAMA_OCR_MODEL).strip()
    try:
        names = await _installed_model_names(ollama)
    except BaseException as ex:
        return False, f"Ollama not reachable: {ollama_error_message(ex)}"
    if not model_name_installed(names, model_name):
        return False, f"Model not installed: {model_name} (run: ollama pull {model_name})"
    return True, "Ready"


def check_ollama_ocr_ready_sync(model: str | None = None) -> tuple[bool, str]:
    model_name = (model or config.OCR_MODEL or DEFAULT_OLLAMA_OCR_MODEL).strip()
    try:
        client = _sync_client()
        lr = client.list()
        names = sorted({str(m.model) for m in lr.models if getattr(m, "model", None)})
    except BaseException as ex:
        return False, f"Ollama not reachable: {ollama_error_message(ex)}"
    if not model_name_installed(names, model_name):
        return False, f"Model not installed: {model_name} (run: ollama pull {model_name})"
    return True, "Ready"
