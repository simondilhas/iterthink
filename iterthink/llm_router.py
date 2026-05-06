"""Route chat completions: Ollama (local) vs OpenAI-compatible / Anthropic / Gemini (HTTP)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

# Encrypted JSON keys (must match settings_ui / studio defaults)
SECRET_COMPANY_OPENAI = "company_openai"
SECRET_CLOUD_ANTHROPIC = "cloud_anthropic"
SECRET_CLOUD_OPENAI = "cloud_openai"
SECRET_CLOUD_GOOGLE = "cloud_google"

DEFAULT_COMPANY_OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def normalize_openai_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        u = DEFAULT_COMPANY_OPENAI_BASE.rstrip("/")
    return u


def _openai_chat_url(base: str) -> str:
    """``{base}/chat/completions`` — standard OpenAI-compatible pattern."""
    return f"{normalize_openai_base_url(base)}/chat/completions"


def _openai_messages_json_hint(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """OpenAI ``json_object`` format requires the word *json* in the input messages."""
    out = [dict(m) for m in messages]
    joined = "\n".join(str(m.get("content") or "") for m in out).lower()
    if "json" in joined:
        return out
    if out and out[0].get("role") == "system":
        out[0] = {
            **out[0],
            "content": (str(out[0].get("content") or "") + "\n\nRespond with valid JSON only."),
        }
    else:
        out.insert(0, {"role": "system", "content": "Respond with valid JSON only."})
    return out


def _messages_split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    sys_chunks: list[str] = []
    rest: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") == "system":
            sys_chunks.append(str(m.get("content") or ""))
        else:
            rest.append({"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")})
    return "\n\n".join(sys_chunks).strip(), rest


def _gemini_url_fixed(model: str, api_key: str, stream: bool) -> str:
    from urllib.parse import quote

    key_q = quote(api_key, safe="")
    enc_model = model.replace("/", "-")
    if stream:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{enc_model}:streamGenerateContent?alt=sse&key={key_q}"
        )
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{enc_model}:generateContent?key={key_q}"
    )


def _build_gemini_body(messages: list[dict[str, str]], json_mode: bool) -> dict[str, Any]:
    system_text, rest = _messages_split_system(messages)
    contents: list[dict[str, Any]] = []
    for m in rest:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    body: dict[str, Any] = {"contents": contents}
    if system_text:
        body["systemInstruction"] = {"parts": [{"text": system_text}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    return body


async def _openai_nonstream(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> dict[str, Any]:
    msgs = _openai_messages_json_hint(messages) if json_mode else messages
    payload: dict[str, Any] = {"model": model, "messages": msgs}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = await client.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120.0,
    )
    r.raise_for_status()
    data = r.json()
    text = ""
    try:
        text = (data["choices"][0]["message"].get("content")) or ""
    except (KeyError, IndexError, TypeError):
        text = ""
    return {"message": {"content": str(text)}}


async def _openai_stream_iter(
    client: httpx.AsyncClient,
    *,
    url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> AsyncIterator[dict[str, Any]]:
    msgs = _openai_messages_json_hint(messages) if json_mode else messages
    payload: dict[str, Any] = {"model": model, "messages": msgs, "stream": True}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with client.stream(
        "POST",
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            chunk = line[6:].strip()
            if chunk == "[DONE]":
                break
            try:
                data = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            try:
                delta = data["choices"][0].get("delta") or {}
                piece = delta.get("content") or ""
            except (KeyError, IndexError, TypeError):
                piece = ""
            if piece:
                yield {"message": {"content": piece}}


async def _openai_stream_full(
    *,
    url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> AsyncIterator[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        async for part in _openai_stream_iter(
            client, url=url, api_key=api_key, model=model, messages=messages, json_mode=json_mode
        ):
            yield part


async def _anthropic_nonstream(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> dict[str, Any]:
    system_text, rest = _messages_split_system(messages)
    if json_mode and system_text:
        system_text = system_text + "\n\nReply with valid JSON only, no markdown fences."
    elif json_mode:
        system_text = "Reply with valid JSON only, no markdown fences."
    anth_msgs: list[dict[str, str]] = []
    for m in rest:
        if m["role"] in ("user", "assistant"):
            anth_msgs.append({"role": m["role"], "content": m["content"]})
    if not anth_msgs:
        anth_msgs = [{"role": "user", "content": ""}]
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 8192,
        "messages": anth_msgs,
    }
    if system_text:
        payload["system"] = system_text
    r = await client.post(
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json=payload,
        timeout=120.0,
    )
    r.raise_for_status()
    data = r.json()
    parts = data.get("content") or []
    text = ""
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            text += str(p.get("text") or "")
    return {"message": {"content": text}}


async def _anthropic_stream_iter(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> AsyncIterator[dict[str, Any]]:
    system_text, rest = _messages_split_system(messages)
    if json_mode and system_text:
        system_text = system_text + "\n\nReply with valid JSON only, no markdown fences."
    elif json_mode:
        system_text = "Reply with valid JSON only, no markdown fences."
    anth_msgs: list[dict[str, str]] = []
    for m in rest:
        if m["role"] in ("user", "assistant"):
            anth_msgs.append({"role": m["role"], "content": m["content"]})
    if not anth_msgs:
        anth_msgs = [{"role": "user", "content": ""}]
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 8192,
        "messages": anth_msgs,
        "stream": True,
    }
    if system_text:
        payload["system"] = system_text
    async with client.stream(
        "POST",
        ANTHROPIC_API_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json=payload,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "content_block_delta":
                delta = evt.get("delta") or {}
                if delta.get("type") == "text_delta":
                    piece = str(delta.get("text") or "")
                    if piece:
                        yield {"message": {"content": piece}}


async def _anthropic_stream_full(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> AsyncIterator[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        async for part in _anthropic_stream_iter(
            client, api_key=api_key, model=model, messages=messages, json_mode=json_mode
        ):
            yield part


async def _gemini_nonstream(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> dict[str, Any]:
    url = _gemini_url_fixed(model, api_key, stream=False)
    body = _build_gemini_body(messages, json_mode)
    r = await client.post(url, json=body, timeout=120.0)
    r.raise_for_status()
    data = r.json()
    text = ""
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if "text" in part:
                text += str(part["text"] or "")
    return {"message": {"content": text}}


_sse_data = re.compile(r"^data:\s*(.+)$")


async def _gemini_stream_iter(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> AsyncIterator[dict[str, Any]]:
    url = _gemini_url_fixed(model, api_key, stream=True)
    body = _build_gemini_body(messages, json_mode)
    async with client.stream("POST", url, json=body, timeout=120.0) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            m = _sse_data.match(line or "")
            if not m:
                continue
            chunk = m.group(1).strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                data = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            for cand in data.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if "text" in part:
                        piece = str(part.get("text") or "")
                        if piece:
                            yield {"message": {"content": piece}}


async def _gemini_stream_full(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> AsyncIterator[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        async for part in _gemini_stream_iter(
            client, api_key=api_key, model=model, messages=messages, json_mode=json_mode
        ):
            yield part


class LlmChatBackend:
    """
    Duck-compatible with ``ollama.AsyncClient.chat`` for call sites that pass
    ``model``, ``messages``, ``stream``, and optional ``format``.
    """

    def __init__(
        self,
        ollama: Any,
        *,
        tier: str,
        cloud_vendor: str,
        local_model: str,
        company_openai_model: str,
        company_openai_base_url: str,
        cloud_anthropic_model: str,
        cloud_openai_model: str,
        cloud_google_model: str,
        secrets: dict[str, str],
    ) -> None:
        self._ollama = ollama
        self._tier = tier
        self._cloud_vendor = (cloud_vendor or "openai").strip().lower()
        self._local_model = local_model
        self._company_openai_model = company_openai_model
        self._company_base = company_openai_base_url
        self._cloud_anthropic_model = cloud_anthropic_model
        self._cloud_openai_model = cloud_openai_model
        self._cloud_google_model = cloud_google_model
        self._secrets = secrets

    def effective_model(self, explicit: str | None) -> str:
        if explicit and explicit.strip():
            return explicit.strip()
        if self._tier == "local":
            return self._local_model
        if self._tier == "company":
            return self._company_openai_model or "gpt-4o-mini"
        if self._tier == "cloud":
            if self._cloud_vendor == "anthropic":
                return self._cloud_anthropic_model or "claude-3-5-sonnet-20241022"
            if self._cloud_vendor == "google":
                return self._cloud_google_model or "gemini-1.5-flash"
            return self._cloud_openai_model or "gpt-4o-mini"
        return self._local_model

    def _require_secret(self, key: str) -> str:
        v = (self._secrets.get(key) or "").strip()
        if not v:
            raise ValueError(f"Missing API key for {key}. Unlock or save credentials in Settings → Models.")
        return v

    async def chat(
        self,
        *,
        model: str = "",
        messages: list[dict[str, str]] | None = None,
        stream: bool = False,
        format: str | None = None,
    ) -> Any:
        messages = messages or []
        m = self.effective_model(model or None)
        json_mode = format == "json"

        if self._tier == "local":
            kwargs: dict[str, Any] = {"model": m, "messages": messages, "stream": stream}
            if format:
                kwargs["format"] = format
            return await self._ollama.chat(**kwargs)

        if self._tier == "company":
            key = self._require_secret(SECRET_COMPANY_OPENAI)
            url = _openai_chat_url(self._company_base)
            if stream:
                return _openai_stream_full(
                    url=url, api_key=key, model=m, messages=messages, json_mode=json_mode
                )
            async with httpx.AsyncClient() as client:
                return await _openai_nonstream(
                    client, url=url, api_key=key, model=m, messages=messages, json_mode=json_mode
                )

        if self._tier == "cloud":
            if self._cloud_vendor == "anthropic":
                key = self._require_secret(SECRET_CLOUD_ANTHROPIC)
                if stream:
                    return _anthropic_stream_full(
                        api_key=key, model=m, messages=messages, json_mode=json_mode
                    )
                async with httpx.AsyncClient() as client:
                    return await _anthropic_nonstream(
                        client, api_key=key, model=m, messages=messages, json_mode=json_mode
                    )
            if self._cloud_vendor == "google":
                key = self._require_secret(SECRET_CLOUD_GOOGLE)
                if stream:
                    return _gemini_stream_full(
                        api_key=key, model=m, messages=messages, json_mode=json_mode
                    )
                async with httpx.AsyncClient() as client:
                    return await _gemini_nonstream(
                        client, api_key=key, model=m, messages=messages, json_mode=json_mode
                    )
            key = self._require_secret(SECRET_CLOUD_OPENAI)
            url = _openai_chat_url(DEFAULT_COMPANY_OPENAI_BASE)
            if stream:
                return _openai_stream_full(
                    url=url, api_key=key, model=m, messages=messages, json_mode=json_mode
                )
            async with httpx.AsyncClient() as client:
                return await _openai_nonstream(
                    client, url=url, api_key=key, model=m, messages=messages, json_mode=json_mode
                )

        kwargs: dict[str, Any] = {"model": m, "messages": messages, "stream": stream}
        if format:
            kwargs["format"] = format
        return await self._ollama.chat(**kwargs)


def remote_http_error_message(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.text[:400]
        except Exception:
            detail = ""
        return f"HTTP {exc.response.status_code}: {detail or str(exc)}".strip()
    return f"{type(exc).__name__}: {exc}"
