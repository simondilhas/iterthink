"""PBS change audit projection."""

from __future__ import annotations

import time

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from iterthink.db.base import Base


class ContentChange(Base):
    __tablename__ = "content_changes"
    __table_args__ = (
        Index("ix_content_changes_version_revision", "content_version_id", "from_revision", "to_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    content_version_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    lineage_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    change_class: Mapped[str] = mapped_column(String(64), nullable=False)
    change_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    to_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    affected_subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    affected_subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    property_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    property_path_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    artifact_storage_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    detected_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    change_source: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SyncOutbox(Base):
    """Stub: entity rows pending cloud sync."""

    __tablename__ = "sync_outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_table: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False, default="upsert")
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    pushed_at: Mapped[float | None] = mapped_column(nullable=True)


class RagSyncOutbox(Base):
    """Stub: RAG chunk ops pending vector backend sync."""

    __tablename__ = "rag_sync_outbox"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content_version_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    op: Mapped[str] = mapped_column(String(16), nullable=False, default="upsert")
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    pushed_at: Mapped[float | None] = mapped_column(nullable=True)
