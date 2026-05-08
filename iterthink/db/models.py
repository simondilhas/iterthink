"""ORM models: documents and version snapshots metadata."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
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


class CredentialVault(Base):
    """Singleton row (id=1): PBKDF2 salt + Fernet ciphertext of API key JSON."""

    __tablename__ = "credential_vault"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    verifier: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
