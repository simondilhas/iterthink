"""Tests for checks_runner: JSON coercion, cache, and document-level orchestration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from iterthink.checks import Check, CheckSymbol, MetricKey, unchanged_paragraph_payload
from iterthink.services import checks_runner
from iterthink.services.checks_runner import (
    load_cached,
    run_check_for_document,
    save_result,
)


def _minimal_check() -> Check:
    return Check(
        id="unit_test_check",
        label="Unit test",
        accent="#5AB0FF",
        system_prompt="You return JSON.",
        user_template="Compare:\nOLD:\n{old}\nNEW:\n{new}",
        symbol_field="impact_symbol",
        summary_path="project_impact.summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(
            CheckSymbol(symbol="~", label="No meaningful change", color="#9AA0A6"),
            CheckSymbol(symbol="!", label="Change", color="#FF0000"),
        ),
    )


@pytest.mark.parametrize(
    ("raw", "expected_key"),
    [
        ('{"impact_symbol": "~", "project_impact": {"summary": "ok"}}', "impact_symbol"),
        ('```json\n{"impact_symbol": "!"}\n```', "impact_symbol"),
        ('Here is JSON:\n{"impact_symbol": "~", "x": 1}', "impact_symbol"),
    ],
)
def test_coerce_json_accepts_variants(raw: str, expected_key: str) -> None:
    out = checks_runner._coerce_json(raw)
    assert out is not None
    assert out.get(expected_key) is not None


@pytest.mark.parametrize(
    "raw",
    ["", "not json", '{"broken": ', "[]", "42"],
)
def test_coerce_json_rejects_invalid(raw: str) -> None:
    assert checks_runner._coerce_json(raw) is None


def test_save_load_cached_roundtrip(ephemeral_store) -> None:
    check = _minimal_check()
    model = "test-model"
    old = "paragraph a"
    new = "paragraph b"
    payload = {"impact_symbol": "!", "project_impact": {"summary": "cached"}}
    save_result(check.id, old, new, model, payload)
    loaded = load_cached(check.id, old, new, model)
    assert loaded == payload


def test_run_check_skips_blank_pair(ephemeral_store) -> None:
    check = _minimal_check()
    calls: list[tuple[int, dict | None, str | None]] = []

    async def on_progress(idx: int, payload: dict | None, err: str | None) -> None:
        calls.append((idx, payload, err))

    llm = AsyncMock()

    results = asyncio.run(
        run_check_for_document(
            llm,
            model="m",
            check=check,
            pairs=[("   ", "\n\t")],
            on_progress=on_progress,
        )
    )
    assert results == [None]
    assert calls == [(0, None, None)]
    llm.chat.assert_not_called()


def test_run_check_identical_paragraph_no_llm_writes_cache(ephemeral_store) -> None:
    check = _minimal_check()
    llm = AsyncMock()
    text = "Same paragraph body."

    results = asyncio.run(
        run_check_for_document(
            llm,
            model="m1",
            check=check,
            pairs=[(text, text)],
            use_cache=True,
        )
    )
    expected = unchanged_paragraph_payload(check)
    assert results == [expected]
    llm.chat.assert_not_called()
    loaded = load_cached(check.id, text, text, "m1")
    assert loaded == expected


def test_run_check_changed_text_use_cache_false_calls_llm(ephemeral_store) -> None:
    check = _minimal_check()
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value={"message": {"content": '{"impact_symbol": "!", "project_impact": {"summary": "from model"}}'}}
    )
    results = asyncio.run(
        run_check_for_document(
            llm,
            model="m2",
            check=check,
            pairs=[("old text", "new text")],
            use_cache=False,
        )
    )
    assert results and results[0] is not None
    assert results[0].get("impact_symbol") == "!"
    llm.chat.assert_awaited_once()


def test_run_check_cache_hit_avoids_llm(ephemeral_store) -> None:
    check = _minimal_check()
    model = "m3"
    old = "alpha"
    new = "beta"
    primed = {"impact_symbol": "~", "project_impact": {"summary": "primed"}}
    save_result(check.id, old, new, model, primed)
    llm = AsyncMock()

    results = asyncio.run(
        run_check_for_document(
            llm,
            model=model,
            check=check,
            pairs=[(old, new)],
            use_cache=True,
        )
    )
    assert results == [primed]
    llm.chat.assert_not_called()
