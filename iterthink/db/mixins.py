"""Shared ORM mixins for PBS content projection."""

from __future__ import annotations

import time

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    updated_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time(), onupdate=lambda: time.time())


class VersionedLineageMixin:
    lineage_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    supersedes_content_id: Mapped[int | None] = mapped_column(
        ForeignKey("content.id", ondelete="SET NULL"),
        nullable=True,
    )
