"""Workspace / project scope (maps to cloud tenant_id / project_id)."""

from __future__ import annotations

import time

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from iterthink.db.base import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(nullable=False, index=True, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    root_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(nullable=False, default=lambda: time.time())
