"""Offline commercial license: SHA-256 passphrase check, license file in store directory."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from iterthink import config

PRICING_URL = "https://www.iterthink.com/#pricing"

# SHA-256 of the commercial passphrase (hex). Replace before release.
LICENSE_PASSPHRASE_SHA256 = "aef65b851b709e037fabc7dc6f8170660bbb549bbdc678f45088a3ac96d2d095"


def _license_path() -> Path:
    return config.STORE_DIR / "license.dat"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.strip().encode("utf-8")).hexdigest()


def is_licensed() -> bool:
    path = _license_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _sha256(str(data.get("passphrase", ""))) == LICENSE_PASSPHRASE_SHA256
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def activate(passphrase: str) -> bool:
    if _sha256(passphrase) != LICENSE_PASSPHRASE_SHA256:
        return False
    path = _license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"passphrase": passphrase.strip(), "activated_at": time.time()}),
        encoding="utf-8",
    )
    return True


def deactivate() -> None:
    try:
        _license_path().unlink()
    except FileNotFoundError:
        pass
