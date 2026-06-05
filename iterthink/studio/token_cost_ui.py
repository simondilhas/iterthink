"""AI bar token cost label (Office/Cloud tiers)."""

from __future__ import annotations

import flet as ft

from iterthink import config
from iterthink.token_cost_settings import (
    UsageTotals,
    format_cost_with_period,
    format_usage_tooltip,
    load_period,
    remote_tier_applies,
)

from .util import ctrl_on_page as _ctrl_on_page


def build_token_cost_label(*, size: int = 13) -> ft.Text:
    return ft.Text(
        format_cost_with_period(0.0),
        size=size,
        color=config.ON_SURFACE_VARIANT,
        visible=False,
        tooltip="",
    )


def sync_token_cost_display(
    label: ft.Text | None,
    *,
    tier: str,
    totals: UsageTotals,
) -> None:
    if label is None:
        return
    show = remote_tier_applies(tier)
    period = load_period()
    text = format_cost_with_period(totals.cost_usd, period)
    tip = format_usage_tooltip(totals, period)
    changed = False
    if getattr(label, "visible", None) != show:
        label.visible = show
        changed = True
    if getattr(label, "value", None) != text:
        label.value = text
        changed = True
    if getattr(label, "tooltip", None) != tip:
        label.tooltip = tip
        changed = True
    if changed and _ctrl_on_page(label):
        label.update()
