"""RAG index status for Settings and diagnostics."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iterthink import config
from iterthink.persistence import content_repo, store_db
from iterthink.studio.tree import is_excluded_from_doc_tree


def iter_workspace_markdown_paths() -> list[Path]:
    root = config.DOCUMENTS.resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*.md")):
        if is_excluded_from_doc_tree(p):
            continue
        out.append(p.resolve())
    return out


@dataclass(frozen=True)
class RagIndexStatus:
    indexed_documents: int
    total_documents: int
    stale_documents: int
    active_chunks: int
    historical_chunks: int
    index_size_bytes: int | None
    last_indexed_at: float | None


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.0f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _format_timestamp(ts: float | None) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def format_status_line(status: RagIndexStatus) -> str:
    parts = [f"{status.indexed_documents} / {status.total_documents} indexed"]
    if status.stale_documents:
        parts.append(f"{status.stale_documents} stale")
    return " · ".join(parts)


def format_chunks_line(status: RagIndexStatus) -> str:
    parts = [f"{status.active_chunks:,} active", f"{status.historical_chunks:,} historical"]
    if status.index_size_bytes is not None:
        parts.append(_format_bytes(status.index_size_bytes))
    return " · ".join(parts)


def compute_rag_index_status(conn: Any, session: Any) -> RagIndexStatus:
    indexed_documents = int(conn.execute("SELECT COUNT(*) FROM rag_lineage_index").fetchone()[0])
    total_documents = len(iter_workspace_markdown_paths())

    active_chunks = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM rag_child_chunk c
            INNER JOIN rag_lineage_index li
                ON li.lineage_id = c.lineage_id
               AND li.content_version_id = c.content_version_id
            """
        ).fetchone()[0]
    )
    total_chunks = int(conn.execute("SELECT COUNT(*) FROM rag_child_chunk").fetchone()[0])
    historical_chunks = max(0, total_chunks - active_chunks)

    last_row = conn.execute("SELECT MAX(indexed_at) FROM rag_lineage_index").fetchone()
    last_indexed_at = float(last_row[0]) if last_row and last_row[0] is not None else None

    stale_documents = 0
    index_rows = conn.execute(
        "SELECT lineage_id, content_version_id FROM rag_lineage_index"
    ).fetchall()
    for row in index_rows:
        lid = str(row[0])
        indexed_vid = int(row[1])
        latest_vid = content_repo.latest_version_id_for_lineage(session, lid)
        if latest_vid is not None and int(latest_vid) != indexed_vid:
            stale_documents += 1

    index_size_bytes: int | None = None
    try:
        rag_path = config.RAG_DB_PATH
        if rag_path.is_file():
            index_size_bytes = os.path.getsize(rag_path)
            for suffix in ("-wal", "-shm"):
                p = Path(str(rag_path) + suffix)
                if p.is_file():
                    index_size_bytes += os.path.getsize(p)
    except OSError:
        index_size_bytes = None

    return RagIndexStatus(
        indexed_documents=indexed_documents,
        total_documents=total_documents,
        stale_documents=stale_documents,
        active_chunks=active_chunks,
        historical_chunks=historical_chunks,
        index_size_bytes=index_size_bytes,
        last_indexed_at=last_indexed_at,
    )


def is_lineage_index_stale(session: Any, lineage_id: str, indexed_version_id: int) -> bool:
    latest_vid = content_repo.latest_version_id_for_lineage(session, lineage_id)
    if latest_vid is None:
        return False
    return int(latest_vid) != int(indexed_version_id)


def format_last_indexed_line(status: RagIndexStatus) -> str:
    return f"Last indexed: {_format_timestamp(status.last_indexed_at)}"
