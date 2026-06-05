"""Filesystem project scope for workspace RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iterthink import config


def project_slug_for_path(resolved: Path) -> str | None:
    """First path segment under ``config.DOCUMENTS``, or ``None`` for root-level docs."""
    root = config.DOCUMENTS.resolve()
    try:
        rel = resolved.resolve().relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) >= 2:
        return parts[0]
    return None


def project_scope_from_lineage(
    session: Any,
    resolved: Path,
    lineage_row: Any,
) -> tuple[int, str | None]:
    """Return ``(project_id, project_slug)`` for RAG indexing."""
    del session
    project_id = int(getattr(lineage_row, "project_id", 1) or 1)
    return project_id, project_slug_for_path(resolved)
