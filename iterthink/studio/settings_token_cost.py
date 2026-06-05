"""Settings → Usage tab (token cost aggregation period)."""

from __future__ import annotations

from typing import Any, Callable

import flet as ft
import yaml

from iterthink import config
from iterthink.persistence import store_db
from iterthink.token_cost_settings import (
    PERIOD_LABELS,
    format_cost_usd,
    format_usage_tooltip,
    load_period,
    normalize_period,
    period_start_timestamp,
)
from iterthink.db.session import session_scope
from iterthink.persistence import token_usage


def _ctrl_on_page(ctrl: ft.Control) -> bool:
    try:
        return ctrl.page is not None
    except RuntimeError:
        return False


def build_token_cost_settings_tab(
    *,
    studio: Any,
    bootstrap_data: Callable[[], dict],
    on_period_changed: Callable[[], None] | None = None,
) -> ft.Container:
    _bd0 = bootstrap_data()
    _p0 = normalize_period(_bd0.get("token_cost_period", config.TOKEN_COST_PERIOD))

    period_seg = ft.SegmentedButton(
        selected=[_p0],
        segments=[
            ft.Segment(value="day", label="Day"),
            ft.Segment(value="month", label="Month"),
            ft.Segment(value="year", label="Year"),
        ],
    )

    summary_txt = ft.Text("", size=12, color=config.ON_SURFACE_SOFT)

    def _refresh_summary() -> None:
        period = normalize_period(config.TOKEN_COST_PERIOD)
        since = period_start_timestamp(period)
        with session_scope() as session:
            totals = token_usage.aggregate_cost(session, since)
        label = PERIOD_LABELS.get(period, period)
        summary_txt.value = (
            f"{label}: {format_cost_usd(totals.cost_usd)} · "
            f"{format_usage_tooltip(totals, period).split(chr(10), 1)[-1]}"
        )
        if _ctrl_on_page(summary_txt):
            summary_txt.update()

    def _persist_period(raw: str) -> None:
        period = normalize_period(raw)
        config.TOKEN_COST_PERIOD = period
        data = bootstrap_data()
        data["token_cost_period"] = period
        dumped = yaml.safe_dump(
            data,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=88,
        )
        from iterthink import config as cfg_mod

        cfg_mod.write_bootstrap_yaml_text(dumped)
        store_db.settings_set(studio._db, store_db.SETTINGS_TOKEN_COST_PERIOD, period)
        _refresh_summary()
        if on_period_changed is not None:
            on_period_changed()

    def _on_period_change(_e: ft.ControlEvent | None = None) -> None:
        sel = list(getattr(period_seg, "selected", []) or [])
        if not sel:
            return
        _persist_period(str(sel[0]))

    period_seg.on_change = _on_period_change
    _refresh_summary()

    return ft.Container(
        padding=8,
        content=ft.Column(
            [
                ft.Text("Usage", size=18, weight=ft.FontWeight.W_600),
                ft.Text("Counter period", size=14, weight=ft.FontWeight.W_600),
                period_seg,
                summary_txt,
            ],
            tight=True,
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
