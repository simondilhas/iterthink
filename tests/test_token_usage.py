"""Tests for LLM usage persistence and aggregation."""

from __future__ import annotations

import time

from iterthink.db.base import Base
from iterthink.db import models  # noqa: F401
from iterthink.persistence import token_usage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def test_record_and_aggregate() -> None:
    session = _session()
    now = time.time()
    token_usage.record_usage(
        session,
        tier="cloud",
        vendor="openai",
        model="gpt-4o-mini",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    token_usage.record_usage(
        session,
        tier="company",
        vendor="openai",
        model="gpt-4o-mini",
        prompt_tokens=200,
        completion_tokens=100,
    )
    totals = token_usage.aggregate_cost(session, now - 60)
    assert totals.prompt_tokens == 1200
    assert totals.completion_tokens == 600
    assert totals.cost_usd > 0


def test_aggregate_respects_since_filter() -> None:
    session = _session()
    row = token_usage.record_usage(
        session,
        tier="cloud",
        vendor="openai",
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
    )
    future = row.created_at + 3600
    totals = token_usage.aggregate_cost(session, future)
    assert totals.prompt_tokens == 0
    assert totals.completion_tokens == 0
    assert totals.cost_usd == 0.0
