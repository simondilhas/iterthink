"""Small helpers shared by studio UI modules."""

from __future__ import annotations

import flet as ft

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
