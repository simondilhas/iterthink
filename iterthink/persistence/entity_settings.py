"""App settings in entity DB (``app_settings`` table)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink.db.models import AppSetting


def settings_get(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row is not None else None


def settings_set(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    session.commit()
