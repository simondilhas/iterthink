"""Tests for KI Analyse check override helpers and runner skip behavior."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from iterthink import checks as checks_mod
from iterthink.services import checks_runner


def _minimal_check() -> checks_mod.Check:
    return checks_mod.Check(
        id="unit_override_check",
        label="Unit override",
        accent="#5AB0FF",
        system_prompt="Return JSON.",
        user_template="OLD:\n{old}\nNEW:\n{new}",
        symbol_field="symbol",
        summary_path="summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(
            checks_mod.CheckSymbol(symbol="~", label="Neutral", color="#9AA0A6"),
            checks_mod.CheckSymbol(symbol="!", label="Change", color="#E5484D"),
        ),
    )


def test_model_helpers_preserve_suggestion_after_override() -> None:
    check = _minimal_check()
    base = {
        "symbol": "~",
        "summary": "Model summary",
        "recommendations": [
            {"action": "Model rec"},
            {"action": "Second rec"},
        ],
    }
    patched = checks_mod.apply_check_override(
        base, check, symbol="!", recommendation="Human rec"
    )
    assert checks_mod.model_symbol(check, patched) == "~"
    assert checks_mod.model_summary(check, patched) == "Model summary"
    assert checks_mod.model_primary_recommendation(patched) == "Model rec"
    model_recs = checks_mod.model_recommendations(patched, limit=3)
    assert len(model_recs) == 2
    assert model_recs[0]["action"] == "Model rec"
    assert model_recs[1]["action"] == "Second rec"


def test_apply_and_clear_check_override() -> None:
    check = _minimal_check()
    base = {
        "symbol": "~",
        "summary": "Model summary",
        "recommendations": [{"action": "Model rec"}],
    }
    patched = checks_mod.apply_check_override(
        base, check, symbol="!", recommendation="Human rec"
    )
    assert checks_mod.is_overridden(patched)
    assert checks_mod.effective_symbol(check, patched) == "!"
    assert checks_mod.effective_primary_recommendation(patched) == "Human rec"
    cleared = checks_mod.clear_check_override(patched, check)
    assert not checks_mod.is_overridden(cleared)
    assert checks_mod.effective_symbol(check, cleared) == "~"
    assert checks_mod.effective_primary_recommendation(cleared) == "Model rec"


@pytest.mark.usefixtures("ephemeral_store")
def test_save_load_override_roundtrip() -> None:
    check = _minimal_check()
    old = "Old paragraph"
    new = "New paragraph"
    model = "test-model"
    payload = checks_mod.apply_check_override(
        {"symbol": "~", "summary": "s", "recommendations": [{"action": "a"}]},
        check,
        symbol="!",
        recommendation="override text",
    )
    checks_runner.save_result(check.id, old, new, model, payload)
    loaded = checks_runner.load_cached(check.id, old, new, model)
    assert loaded is not None
    assert checks_mod.is_overridden(loaded)
    assert checks_mod.effective_symbol(check, loaded) == "!"


@pytest.mark.usefixtures("ephemeral_store")
def test_run_check_skips_overridden_paragraph() -> None:
    check = _minimal_check()
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value={
            "message": {
                "content": json.dumps(
                    {"symbol": "!", "summary": "LLM", "recommendations": [{"action": "LLM rec"}]}
                )
            }
        }
    )
    pairs = [("Overridden old.", "Overridden new."), ("Fresh old.", "Fresh new.")]
    overridden = checks_mod.apply_check_override(
        {"symbol": "~", "summary": "kept", "recommendations": [{"action": "Human"}]},
        check,
        symbol="~",
        recommendation="Human",
    )
    checks_runner.save_result(check.id, pairs[0][0], pairs[0][1], "m", overridden)

    results = asyncio.run(
        checks_runner.run_check_for_document(
            llm,
            model="m",
            check=check,
            pairs=pairs,
            use_cache=True,
        )
    )
    assert llm.chat.await_count == 1
    assert results[0] is not None
    assert checks_mod.is_overridden(results[0])
    assert results[1] is not None
    assert results[1].get("symbol") == "!"
