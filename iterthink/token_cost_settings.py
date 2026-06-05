"""Token cost period, model pricing, and display helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

from iterthink import config

UsagePeriod = Literal["day", "month", "year"]

_DEFAULTS_PATH = Path(__file__).resolve().parent / "defaults" / "token_pricing.yaml"

PERIOD_LABELS: dict[UsagePeriod, str] = {
    "day": "Today",
    "month": "This month",
    "year": "This year",
}

PERIOD_UNITS: dict[UsagePeriod, str] = {
    "day": "/d",
    "month": "/m",
    "year": "/a",
}


@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    output_per_million: float


@dataclass(frozen=True)
class UsageTotals:
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def normalize_period(raw: str | None) -> UsagePeriod:
    s = (raw or "").strip().lower()
    if s in ("day", "month", "year"):
        return s  # type: ignore[return-value]
    return "year"


def load_period() -> UsagePeriod:
    return normalize_period(config.TOKEN_COST_PERIOD)


def period_start(period: UsagePeriod, *, now: datetime | None = None) -> datetime:
    dt = now or datetime.now().astimezone()
    if period == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def period_start_timestamp(period: UsagePeriod | None = None) -> float:
    p = period or load_period()
    return period_start(p).timestamp()


_pricing_cache: dict[str, ModelPricing] | None = None
_default_pricing: ModelPricing | None = None


def _load_pricing_yaml() -> tuple[ModelPricing, dict[str, ModelPricing]]:
    raw = yaml.safe_load(_DEFAULTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return ModelPricing(0.50, 1.50), {}
    def_row = raw.get("default") if isinstance(raw.get("default"), dict) else {}
    default = ModelPricing(
        float(def_row.get("input_per_million", 0.50)),
        float(def_row.get("output_per_million", 1.50)),
    )
    models_raw = raw.get("models")
    out: dict[str, ModelPricing] = {}
    if isinstance(models_raw, dict):
        for mid, row in models_raw.items():
            if not isinstance(row, dict):
                continue
            out[str(mid).strip().lower()] = ModelPricing(
                float(row.get("input_per_million", default.input_per_million)),
                float(row.get("output_per_million", default.output_per_million)),
            )
    return default, out


def _ensure_pricing_loaded() -> None:
    global _pricing_cache, _default_pricing
    if _pricing_cache is not None:
        return
    _default_pricing, _pricing_cache = _load_pricing_yaml()


def pricing_for_model(model: str) -> ModelPricing:
    _ensure_pricing_loaded()
    assert _default_pricing is not None and _pricing_cache is not None
    key = (model or "").strip().lower()
    if key in _pricing_cache:
        return _pricing_cache[key]
    for mid, p in _pricing_cache.items():
        if key.startswith(mid) or mid.startswith(key):
            return p
    return _default_pricing


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = pricing_for_model(model)
    pt = max(0, int(prompt_tokens))
    ct = max(0, int(completion_tokens))
    return (pt * p.input_per_million + ct * p.output_per_million) / 1_000_000.0


def format_cost_usd(amount: float) -> str:
    return f"${max(0.0, float(amount)):.2f}"


def format_cost_with_period(amount: float, period: UsagePeriod | None = None) -> str:
    p = period or load_period()
    unit = PERIOD_UNITS.get(p, f"/{p}")
    return f"{format_cost_usd(amount)}{unit}"


def format_token_count(n: int) -> str:
    v = max(0, int(n))
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.1f}k"
    return str(v)


def format_usage_tooltip(totals: UsageTotals, period: UsagePeriod | None = None) -> str:
    p = period or load_period()
    label = PERIOD_LABELS.get(p, p)
    return (
        f"{label}: {format_cost_usd(totals.cost_usd)}\n"
        f"In {format_token_count(totals.prompt_tokens)}, "
        f"out {format_token_count(totals.completion_tokens)}, "
        f"total {format_token_count(totals.total_tokens)} tokens"
    )


def remote_tier_applies(tier: str) -> bool:
    return (tier or "").strip().lower() in ("company", "cloud")
