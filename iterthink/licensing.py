"""Online commercial license via iterthink-api with local cache and grace period."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from iterthink import config

PRICING_URL = "https://www.iterthink.com/#pricing"
REVALIDATE_SECONDS = 6 * 3600
GRACE_SECONDS = 7 * 24 * 3600


def api_base_url() -> str:
    return os.environ.get("ITER_THINK_API_URL", "https://api.iterthink.com").rstrip("/")


def _license_path() -> Path:
    return config.STORE_DIR / "license.dat"


def _machine_id_path() -> Path:
    return config.STORE_DIR / "machine.id"


def machine_id() -> str:
    path = _machine_id_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    mid = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(mid, encoding="utf-8")
    return mid


def _read_cache() -> dict[str, Any]:
    try:
        data = json.loads(_license_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_cache(data: dict[str, Any]) -> None:
    path = _license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=0), encoding="utf-8")


def get_license_key() -> str:
    return str(_read_cache().get("license_key", "")).strip()


def swiss_ai_active() -> bool:
    return bool(_read_cache().get("swiss_ai_active"))


def is_licensed() -> bool:
    cache = _read_cache()
    if not cache.get("licensed"):
        return False
    validated_at = float(cache.get("validated_at") or 0)
    if time.time() - validated_at < REVALIDATE_SECONDS:
        return True
    return _revalidate_online(grace_only=True)


def _apply_status(data: dict[str, Any], license_key: str) -> None:
    _write_cache(
        {
            "license_key": license_key,
            "licensed": bool(data.get("licensed")),
            "plan": data.get("plan", ""),
            "swiss_ai_active": bool(data.get("swiss_ai_active")),
            "validated_at": time.time(),
        }
    )


def _revalidate_online(*, grace_only: bool) -> bool:
    key = get_license_key()
    if not key:
        return False
    cache = _read_cache()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{api_base_url()}/v1/licenses/status",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code == 200:
            _apply_status(resp.json(), key)
            return True
    except httpx.HTTPError:
        pass
    if grace_only:
        validated_at = float(cache.get("validated_at") or 0)
        if cache.get("licensed") and time.time() - validated_at < GRACE_SECONDS:
            return True
    return False


def activate(license_key: str) -> bool:
    key = (license_key or "").strip()
    if not key:
        return False
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{api_base_url()}/v1/licenses/activate",
                json={"license_key": key, "machine_id": machine_id()},
            )
        if resp.status_code != 200:
            return False
        _apply_status(resp.json(), key)
        return True
    except httpx.HTTPError:
        return False


def deactivate() -> None:
    try:
        _license_path().unlink()
    except FileNotFoundError:
        pass


def fetch_swiss_ai_usage() -> dict[str, Any] | None:
    key = get_license_key()
    if not key or not swiss_ai_active():
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{api_base_url()}/v1/ai/usage",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


def fetch_latest_update() -> dict[str, Any] | None:
    key = get_license_key()
    if not key or not is_licensed():
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{api_base_url()}/v1/updates/latest",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None
