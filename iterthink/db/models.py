"""ORM models: PBS content projection, annotations, credentials.

RAG vectors live in ``store.rag.sqlite3`` via ``iterthink.persistence.store_db``.
"""

from __future__ import annotations

import time

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from iterthink.db.base import Base

# Register content graph on same metadata
from iterthink.db import change_models as _change_models  # noqa: F401
from iterthink.db import content_models as _content_models  # noqa: F401
from iterthink.db import workspace as _workspace  # noqa: F401


class AppSetting(Base):
    """Key/value settings (entity DB; was in combined store.sqlite3)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class ParagraphUserComment(Base):
    """User note per paragraph or plan-PDF pin/cloud for a content version."""

    __tablename__ = "paragraph_user_comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content_version_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    paragraph_index: Mapped[int] = mapped_column(Integer, nullable=False)
    annotation_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="paragraph")
    plan_page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_norm_x: Mapped[float | None] = mapped_column(nullable=True)
    plan_norm_y: Mapped[float | None] = mapped_column(nullable=True)
    geometry_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    updated_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (
        Index(
            "uq_paragraph_user_comment_paragraph",
            "content_version_id",
            "paragraph_index",
            unique=True,
            sqlite_where=text("annotation_kind = 'paragraph'"),
        ),
        Index(
            "ix_paragraph_user_comments_plan_ver",
            "content_version_id",
            "annotation_kind",
        ),
    )


class ImpactAnnotation(Base):
    """Per-paragraph Impact tab LLM output, keyed by content version."""

    __tablename__ = "impact_annotations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    content_version_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    paragraph_index: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    override_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    updated_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (
        UniqueConstraint(
            "content_version_id",
            "paragraph_index",
            "prompt_id",
            name="uq_impact_annotation_key",
        ),
    )


class ParagraphAnalysis(Base):
    """Cache of LLM check results keyed by path key + paragraph content hashes."""

    __tablename__ = "paragraph_analysis"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    old_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    new_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    document_path_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (
        UniqueConstraint(
            "check_id",
            "old_sha256",
            "new_sha256",
            "model",
            "document_path_key",
            name="uq_paragraph_analysis_path_key",
        ),
    )


class CredentialVault(Base):
    """Singleton row (id=1): PBKDF2 salt + Fernet ciphertext of API key JSON."""

    __tablename__ = "credential_vault"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    kdf_salt: Mapped[bytes] = mapped_column(nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(nullable=False)
    verifier: Mapped[bytes] = mapped_column(nullable=False)


class LlmUsageEvent(Base):
    """Remote LLM API token usage (Office/Cloud tiers) for cost aggregation."""

    __tablename__ = "llm_usage_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (Index("ix_llm_usage_events_created_at", "created_at"),)
