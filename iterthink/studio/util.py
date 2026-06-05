"""Small helpers shared by studio UI modules."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

import flet as ft

# ---------------------------------------------------------------------------
# Flet ≥ 0.90 removed the ft.padding helper functions (all / symmetric / only).
# Patch them back onto the module so all call sites keep working unchanged.
# ---------------------------------------------------------------------------
import types as _types

def _ensure_padding_helpers() -> None:
    pad = ft.padding  # type: ignore[attr-defined]
    if callable(getattr(pad, "all", None)):
        return  # already present, nothing to do

    _Padding = ft.Padding  # type: ignore[attr-defined]

    if not isinstance(pad, _types.ModuleType):
        # In some builds ft.padding is the Padding class itself; wrap in a namespace.
        pad = _types.SimpleNamespace()
        ft.padding = pad  # type: ignore[attr-defined]

    def _all(value: float) -> object:
        return _Padding(left=value, top=value, right=value, bottom=value)

    def _symmetric(*, horizontal: float = 0, vertical: float = 0) -> object:
        return _Padding(left=horizontal, right=horizontal, top=vertical, bottom=vertical)

    def _only(*, left: float = 0, top: float = 0, right: float = 0, bottom: float = 0) -> object:
        return _Padding(left=left, top=top, right=right, bottom=bottom)

    pad.all = _all  # type: ignore[assignment]
    pad.symmetric = _symmetric  # type: ignore[assignment]
    pad.only = _only  # type: ignore[assignment]


_ensure_padding_helpers()

# KI routing tier (persisted as ``store_db.SETTINGS_KI_TIER``).
KI_TIER_LOCAL = "local"
KI_TIER_COMPANY = "company"
KI_TIER_CLOUD = "cloud"
KI_TIERS: tuple[str, ...] = (KI_TIER_LOCAL, KI_TIER_COMPANY, KI_TIER_CLOUD)

# When tier is ``cloud``: which remote family to use.
CLOUD_VENDOR_ANTHROPIC = "anthropic"
CLOUD_VENDOR_OPENAI = "openai"
CLOUD_VENDOR_GOOGLE = "google"
CLOUD_VENDORS: tuple[str, ...] = (CLOUD_VENDOR_ANTHROPIC, CLOUD_VENDOR_OPENAI, CLOUD_VENDOR_GOOGLE)


def normalize_save_file_path(
    dest: str,
    *,
    default_file_name: str,
    expected_suffix: str,
) -> Path:
    """Normalize native save-dialog paths (file:// URI, folder-only picks, missing suffix)."""
    raw = (dest or "").strip()
    if not raw:
        raise ValueError("empty path")
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    suffix = expected_suffix if expected_suffix.startswith(".") else f".{expected_suffix}"
    name = default_file_name.strip() or f"export{suffix}"
    if not name.lower().endswith(suffix.lower()):
        name = f"{Path(name).stem}{suffix}"

    p = Path(raw).expanduser()
    if p.is_dir():
        p = p / name
    elif p.suffix.lower() != suffix.lower():
        p = p.with_suffix(suffix) if p.name else p / name
    return p.resolve()


def ctrl_on_page(ctrl: ft.Control) -> bool:
    """Flet raises RuntimeError when reading .page before the control is mounted."""
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


async def safe_list_scroll(
    lv: ft.ListView | None,
    offset: float,
    *,
    duration: int = 0,
) -> None:
    """scroll_to on a mounted, visible ListView; ignore Flet timeouts."""
    if lv is None or not ctrl_on_page(lv) or not bool(getattr(lv, "visible", True)):
        return
    try:
        await lv.scroll_to(offset=offset, duration=duration)
    except (RuntimeError, TimeoutError, TypeError, AttributeError, ValueError):
        pass


def normalize_ki_tier(raw: str | None) -> str:
    s = (raw or KI_TIER_LOCAL).strip().lower()
    return s if s in KI_TIERS else KI_TIER_LOCAL


def normalize_cloud_vendor(raw: str | None) -> str:
    s = (raw or CLOUD_VENDOR_OPENAI).strip().lower()
    return s if s in CLOUD_VENDORS else CLOUD_VENDOR_OPENAI
