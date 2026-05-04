"""Ollama response parsing (no UI imports)."""

from typing import Any

from ollama import ResponseError


def message_content(msg: Any) -> str:
    if msg is None:
        return ""
    if isinstance(msg, dict):
        return str(msg.get("content") or "")
    return str(getattr(msg, "content", None) or "")


def chat_response_text(resp: Any) -> str:
    if resp is None:
        return ""
    if isinstance(resp, dict):
        return message_content(resp.get("message"))
    msg = getattr(resp, "message", None)
    return message_content(msg)


def chat_stream_delta(part: Any) -> str:
    return chat_response_text(part)


def ollama_error_message(exc: BaseException) -> str:
    if isinstance(exc, ResponseError):
        return str(exc).strip() or repr(exc)
    return f"{type(exc).__name__}: {exc}"
