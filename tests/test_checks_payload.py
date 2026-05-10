"""Tests for unchanged_paragraph_payload shape (no LLM path)."""

from __future__ import annotations

from iterthink.checks import Check, CheckSymbol, MetricKey, unchanged_paragraph_payload


def test_unchanged_payload_summary_path_only() -> None:
    check = Check(
        id="simple",
        label="Simple",
        accent="#000",
        system_prompt="s",
        user_template="{old}{new}",
        symbol_field="impact_symbol",
        summary_path="project_impact.summary",
        metrics_path="",
        metric_keys=(),
        metric_value_set=(),
        symbol_set=(CheckSymbol(symbol="~", label="Neutral", color="#ccc"),),
    )
    out = unchanged_paragraph_payload(check)
    assert out["impact_symbol"] == "~"
    assert out["project_impact"]["summary"] == "Paragraph text is unchanged; analysis skipped."


def test_unchanged_payload_dgnb_style_metrics_block() -> None:
    check = Check(
        id="project",
        label="Project",
        accent="#000",
        system_prompt="s",
        user_template="{old}{new}",
        symbol_field="impact_symbol",
        summary_path="project_impact.metrics.summary",
        metrics_path="project_impact.metrics",
        metric_keys=(
            MetricKey(key="cost", label="Cost"),
            MetricKey(key="schedule", label="Schedule"),
        ),
        metric_value_set=("None", "Low", "Medium", "High"),
        symbol_set=(CheckSymbol(symbol="~", label="No meaningful change", color="#ccc"),),
    )
    out = unchanged_paragraph_payload(check)
    assert out["technical_label"] == "Editorial change"
    metrics = out["project_impact"]["metrics"]
    assert metrics["cost"] == "None"
    assert metrics["schedule"] == "None"
    assert metrics["summary"] == "Paragraph text is unchanged; analysis skipped."
