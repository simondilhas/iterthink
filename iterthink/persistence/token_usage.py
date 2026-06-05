"""Persist and aggregate remote LLM usage events."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from iterthink.db.models import LlmUsageEvent
from iterthink.token_cost_settings import UsageTotals, compute_cost


def record_usage(
    session: Session,
    *,
    tier: str,
    vendor: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> LlmUsageEvent:
    pt = max(0, int(prompt_tokens))
    ct = max(0, int(completion_tokens))
    cost = compute_cost(model, pt, ct)
    row = LlmUsageEvent(
        tier=(tier or "").strip().lower(),
        vendor=(vendor or "").strip().lower(),
        model=(model or "").strip(),
        prompt_tokens=pt,
        completion_tokens=ct,
        cost_usd=cost,
    )
    session.add(row)
    session.commit()
    return row


def aggregate_cost(session: Session, since_ts: float) -> UsageTotals:
    row = session.execute(
        select(
            func.coalesce(func.sum(LlmUsageEvent.cost_usd), 0.0),
            func.coalesce(func.sum(LlmUsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.completion_tokens), 0),
        ).where(LlmUsageEvent.created_at >= float(since_ts))
    ).one()
    return UsageTotals(
        cost_usd=float(row[0] or 0.0),
        prompt_tokens=int(row[1] or 0),
        completion_tokens=int(row[2] or 0),
    )
