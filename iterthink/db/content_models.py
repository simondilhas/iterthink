"""PBS adapter projection: content, files, relations, geometries."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from iterthink.contract.version import CANONICAL_TYPE_VERSION
from iterthink.db.base import Base
from iterthink.db.mixins import TimestampMixin, VersionedLineageMixin

if TYPE_CHECKING:
    pass


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    storage_relpath: Mapped[str] = mapped_column(String(512), nullable=False)
    media_format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    __table_args__ = (
        UniqueConstraint("workspace_id", "project_id", "storage_relpath", name="uq_files_storage"),
    )


class Content(Base, TimestampMixin, VersionedLineageMixin):
    """Versioned entity row; text/plan artifacts use content_kind=artifact."""

    __tablename__ = "content"
    __table_args__ = (
        Index("ix_content_workspace_project_kind_latest", "workspace_id", "project_id", "content_kind", "is_latest"),
        Index(
            "ix_content_workspace_project_lineage_version",
            "workspace_id",
            "project_id",
            "lineage_id",
            "version_no",
            unique=True,
        ),
        Index(
            "ix_content_workspace_project_lineage_latest",
            "workspace_id",
            "project_id",
            "lineage_id",
            unique=True,
            sqlite_where=text("is_latest"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    contract_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    ingestion_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    content_kind: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    canonical_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    canonical_type_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CANONICAL_TYPE_VERSION
    )
    code_or_number: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    storey: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source_system: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    attributes: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lineage-level disk sync (latest artifact only); JSON in attributes could duplicate — kept explicit.
    last_disk_mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_disk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_disk_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    file_links: Mapped[list["ContentFileLink"]] = relationship(
        "ContentFileLink",
        back_populates="content",
        cascade="all, delete-orphan",
    )
    geometries: Mapped[list["ContentGeometry"]] = relationship(
        "ContentGeometry",
        back_populates="content",
        cascade="all, delete-orphan",
    )


class ContentFileLink(Base):
    __tablename__ = "content_file_links"
    __table_args__ = (
        UniqueConstraint("content_id", "file_id", "relation_type", name="uq_content_file_relation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False, default="source_file")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())

    content: Mapped["Content"] = relationship("Content", back_populates="file_links")
    file: Mapped["FileRecord"] = relationship("FileRecord")


class ContentGeometry(Base, TimestampMixin):
    __tablename__ = "content_geometries"
    __table_args__ = (
        Index("ix_content_geometries_content_role", "content_id", "geometry_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True)
    geometry_role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    geometry_source: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    geometry_space: Mapped[str] = mapped_column(String(100), nullable=False, index=True, default="plan_norm")
    geom: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    content: Mapped["Content"] = relationship("Content", back_populates="geometries")


class ContentRelation(Base, TimestampMixin, VersionedLineageMixin):
    __tablename__ = "content_relations"
    __table_args__ = (
        Index(
            "ix_content_relations_workspace_project_lineage_version",
            "workspace_id",
            "project_id",
            "lineage_id",
            "version_no",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    source_content_id: Mapped[int] = mapped_column(ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True)
    target_content_id: Mapped[int] = mapped_column(ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    attributes: Mapped[str | None] = mapped_column(Text, nullable=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
