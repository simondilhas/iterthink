"""ORM models: documents and version snapshots metadata."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from iterthink.db.base import Base

if TYPE_CHECKING:
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    resolved_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    versions: Mapped[list["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_relpath: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_label: Mapped[str | None] = mapped_column(String(256), nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="versions")


class ParagraphAnalysis(Base):
    """Cache of LLM check results keyed by content hashes (shared across documents)."""

    __tablename__ = "paragraph_analysis"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    old_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    new_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (
        UniqueConstraint(
            "check_id", "old_sha256", "new_sha256", "model",
            name="uq_paragraph_analysis_key",
        ),
    )
