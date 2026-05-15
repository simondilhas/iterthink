"""ORM models: documents and version snapshots metadata only.

Embeddings / Impact RAG (sqlite-vec) are not defined here. Start from:

- ``iterthink.persistence.store_db`` — loads sqlite-vec, ``init_schema()`` creates
  ``paragraph_vec`` (vec0), embedding cache, manifest, ``impact_version_chunk``.
- ``iterthink.compare.paragraph_semantics`` — ``embed_texts_cached()`` writes vectors.
- ``iterthink.services.rag.impact_rag`` — project-file and version-scoped ingest
  and retrieval over those tables.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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
    # Last canonical .md state after app read/write (detect external edits via stat + sha).
    last_disk_mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_disk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_disk_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        passive_deletes=True,
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
    pdf_asset_relpath: Mapped[str | None] = mapped_column(String(512), nullable=True)
    docx_asset_relpath: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # When a PDF was imported: "text" (markdown-first) or "plan" (picture-first). None = legacy row.
    pdf_profile: Mapped[str | None] = mapped_column(String(16), nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="versions")


class ParagraphUserComment(Base):
    """User-authored note per markdown paragraph slot for a specific snapshot version."""

    __tablename__ = "paragraph_user_comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paragraph_index: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
    updated_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_id",
            "paragraph_index",
            name="uq_paragraph_user_comment_key",
        ),
    )


class ImpactAnnotation(Base):
    """Per-paragraph Impact tab LLM output (Review → Impact), keyed by snapshot version."""

    __tablename__ = "impact_annotations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
            "document_id",
            "version_id",
            "paragraph_index",
            "prompt_id",
            name="uq_impact_annotation_key",
        ),
    )


class ParagraphAnalysis(Base):
    """Cache of LLM check results keyed by document path + paragraph content hashes."""

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
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    verifier: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
