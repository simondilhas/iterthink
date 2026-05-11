"""Fetch chat model id lists from OpenAI-compatible, Anthropic, and Google APIs."""

from __future__ import annotations

from typing import Any

import httpx

from iterthink.ai.llm_router import ANTHROPIC_VERSION, normalize_openai_base_url


def _openai_models_url(base_url: str) -> str:
    return f"{normalize_openai_base_url(base_url)}/models"


async def fetch_openai_compatible_models(base_url: str, api_key: str) -> tuple[list[str], str | None]:
    """
    ``GET {base}/models`` (OpenAI-compatible). Returns sorted ids, or ``([], error)``.
    """
    key = (api_key or "").strip()
    if not key:
        return [], "API key required (paste key or unlock vault) to list models."
    url = _openai_models_url(base_url)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                timeout=45.0,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as exc:
        return [], f"List models HTTP {exc.response.status_code}: {(exc.response.text or '')[:200]}".strip()
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"

    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], "Unexpected /models response (no data array)."

    ids: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            mid = row.get("id")
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
        elif isinstance(row, str) and row.strip():
            ids.append(row.strip())

    # De-dupe, preserve stable order (API often returns newest first)
    seen: set[str] = set()
    ordered: list[str] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered, None


async def fetch_anthropic_models(api_key: str) -> tuple[list[str], str | None]:
    key = (api_key or "").strip()
    if not key:
        return [], "Anthropic API key required to list models."
    url = "https://api.anthropic.com/v1/models"
    headers = {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
    }
    all_ids: list[str] = []
    after_id: str | None = None
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(50):
                params: dict[str, Any] = {"limit": 100}
                if after_id:
                    params["after_id"] = after_id
                r = await client.get(url, headers=headers, params=params, timeout=45.0)
                r.raise_for_status()
                data = r.json()
                rows = data.get("data") if isinstance(data, dict) else None
                if not isinstance(rows, list):
                    return [], "Unexpected Anthropic /v1/models response."
                batch: list[str] = []
                for row in rows:
                    if isinstance(row, dict):
                        mid = row.get("id")
                        if isinstance(mid, str) and mid.strip():
                            batch.append(mid.strip())
                all_ids.extend(batch)
                if not data.get("has_more") or not batch:
                    break
                after_id = data.get("last_id")
                if not isinstance(after_id, str) or not after_id:
                    break
    except httpx.HTTPStatusError as exc:
        return [], f"Anthropic list models HTTP {exc.response.status_code}: {(exc.response.text or '')[:200]}".strip()
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"

    seen: set[str] = set()
    ordered: list[str] = []
    for x in all_ids:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered, None


def _gemini_model_short_name(full_name: str) -> str:
    """``models/gemini-2.5-flash`` -> ``gemini-2.5-flash``."""
    s = (full_name or "").strip()
    if s.startswith("models/"):
        return s[7:]
    return s


async def fetch_google_generative_models(api_key: str) -> tuple[list[str], str | None]:
    key = (api_key or "").strip()
    if not key:
        return [], "Google API key required to list models."
    base = "https://generativelanguage.googleapis.com/v1beta/models"
    all_short: list[str] = []
    page_token: str | None = None
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(20):
                params: dict[str, Any] = {"key": key, "pageSize": 100}
                if page_token:
                    params["pageToken"] = page_token
                r = await client.get(base, params=params, timeout=45.0)
                r.raise_for_status()
                data = r.json()
                models = data.get("models") if isinstance(data, dict) else None
                if not isinstance(models, list):
                    return [], "Unexpected Google models response."
                for m in models:
                    if not isinstance(m, dict):
                        continue
                    name = m.get("name")
                    if not isinstance(name, str):
                        continue
                    methods = m.get("supportedGenerationMethods")
                    if not isinstance(methods, list) or "generateContent" not in methods:
                        continue
                    short = _gemini_model_short_name(name)
                    if short and not short.startswith("embedding") and "embed" not in short.lower():
                        all_short.append(short)
                page_token = data.get("nextPageToken") if isinstance(data, dict) else None
                if not isinstance(page_token, str) or not page_token:
                    break
    except httpx.HTTPStatusError as exc:
        return [], f"Google list models HTTP {exc.response.status_code}: {(exc.response.text or '')[:200]}".strip()
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"

    seen: set[str] = set()
    ordered: list[str] = []
    for x in all_short:
        if x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered, None
