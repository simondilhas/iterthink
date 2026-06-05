"""Tests for RAG enrichment tier gating."""

from __future__ import annotations

from iterthink.services.rag.enrichment import enrichment_allowed_for_tier


def test_enrichment_skip_always_off() -> None:
    for tier in ("local", "company", "cloud"):
        assert enrichment_allowed_for_tier(tier, "skip") is False


def test_enrichment_on_all_tiers() -> None:
    assert enrichment_allowed_for_tier("local", "local") is True
    assert enrichment_allowed_for_tier("company", "local") is True
    assert enrichment_allowed_for_tier("cloud", "local") is True


def test_enrichment_unknown_tier() -> None:
    assert enrichment_allowed_for_tier("invalid", "local") is False
