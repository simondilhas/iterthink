"""Small helpers shared by studio UI modules."""

from __future__ import annotations

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


def ctrl_on_page(ctrl: ft.Control) -> bool:
    """Flet raises RuntimeError when reading .page before the control is mounted."""
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def normalize_ki_tier(raw: str | None) -> str:
    s = (raw or KI_TIER_LOCAL).strip().lower()
    return s if s in KI_TIERS else KI_TIER_LOCAL


def normalize_cloud_vendor(raw: str | None) -> str:
    s = (raw or CLOUD_VENDOR_OPENAI).strip().lower()
    return s if s in CLOUD_VENDORS else CLOUD_VENDOR_OPENAI
