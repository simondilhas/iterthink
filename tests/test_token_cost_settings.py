"""Tests for token cost settings and pricing."""

from __future__ import annotations

from datetime import datetime

import pytest

from iterthink import config
from iterthink.token_cost_settings import (
    compute_cost,
    format_cost_usd,
    format_cost_with_period,
    format_token_count,
    normalize_period,
    period_start,
    pricing_for_model,
    remote_tier_applies,
)


def test_normalize_period_defaults_to_year() -> None:
    assert normalize_period(None) == "year"
    assert normalize_period("invalid") == "year"
    assert normalize_period("day") == "day"
    assert normalize_period("MONTH") == "month"


def test_period_start_year() -> None:
    now = datetime(2026, 6, 4, 15, 30, tzinfo=datetime.now().astimezone().tzinfo)
    start = period_start("year", now=now)
    assert start.year == 2026
    assert start.month == 1
    assert start.day == 1
    assert start.hour == 0


def test_period_start_month() -> None:
    now = datetime(2026, 6, 4, 15, 30, tzinfo=datetime.now().astimezone().tzinfo)
    start = period_start("month", now=now)
    assert start.month == 6
    assert start.day == 1


def test_compute_cost_known_model() -> None:
    cost = compute_cost("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0)
    assert cost == pytest.approx(0.15, rel=1e-6)


def test_compute_cost_unknown_model_uses_default() -> None:
    default = pricing_for_model("unknown-model-xyz")
    cost = compute_cost("unknown-model-xyz", prompt_tokens=1_000_000, completion_tokens=0)
    assert cost == pytest.approx(default.input_per_million, rel=1e-6)


def test_format_cost_usd() -> None:
    assert format_cost_usd(1.234) == "$1.23"
    assert format_cost_usd(-1) == "$0.00"


def test_format_cost_with_period() -> None:
    assert format_cost_with_period(1.5, "day") == "$1.50/d"
    assert format_cost_with_period(2.0, "month") == "$2.00/m"
    assert format_cost_with_period(0.0, "year") == "$0.00/a"


def test_format_token_count() -> None:
    assert format_token_count(500) == "500"
    assert format_token_count(1500) == "1.5k"
    assert format_token_count(2_500_000) == "2.5M"


def test_remote_tier_applies() -> None:
    assert remote_tier_applies("company")
    assert remote_tier_applies("cloud")
    assert not remote_tier_applies("local")


def test_load_period_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from iterthink.token_cost_settings import load_period

    monkeypatch.setattr(config, "TOKEN_COST_PERIOD", "month")
    assert load_period() == "month"
