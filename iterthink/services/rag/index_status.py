"""RAG index status for Settings and diagnostics."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from iterthink import config
from iterthink.db.content_models import Content
from iterthink.persistence import content_repo, store_db
from iterthink.studio.tree import is_excluded_from_doc_tree

_WORKSPACE_MD_COUNT_CACHE: tuple[float, int] | None = None
_WORKSPACE_MD_COUNT_TTL_SEC = 30.0

def clear_workspace_markdown_count_cache() -> None:
    global _WORKSPACE_MD_COUNT_CACHE
    _WORKSPACE_MD_COUNT_CACHE = None


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


def count_workspace_markdown_paths() -> int:
    """Count workspace .md files; cached briefly to avoid repeated rglob in Settings."""
    global _WORKSPACE_MD_COUNT_CACHE
    now = time.monotonic()
    if _WORKSPACE_MD_COUNT_CACHE is not None:
        cached_at, cached_count = _WORKSPACE_MD_COUNT_CACHE
        if now - cached_at < _WORKSPACE_MD_COUNT_TTL_SEC:
            return cached_count
    count = len(iter_workspace_markdown_paths())
    _WORKSPACE_MD_COUNT_CACHE = (now, count)
    return count


def _count_stale_indexed_documents(
    conn: Any, session: Any, *, workspace_lids: set[str] | None = None
) -> int:
    """Stale = indexed PBS version id differs from latest artifact version."""
    if not workspace_lids:
        return 0

    if config.RAG_DB_PATH.resolve() == config.STORE_DB_PATH.resolve():
        placeholders = ",".join("?" * len(workspace_lids))
        row = session.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM rag_lineage_index li
                INNER JOIN content c
                  ON c.lineage_id = li.lineage_id
                 AND c.is_latest = 1
                 AND c.version_no > 0
                WHERE c.id != li.content_version_id
                  AND li.lineage_id IN ({placeholders})
                """
            ),
            tuple(workspace_lids),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    index_rows = conn.execute(
        "SELECT lineage_id, content_version_id FROM rag_lineage_index"
    ).fetchall()
    if not index_rows:
        return 0
    lineage_ids = [str(row[0]) for row in index_rows if str(row[0]) in workspace_lids]
    if not lineage_ids:
        return 0
    latest_rows = session.execute(
        select(Content.lineage_id, Content.id).where(
            Content.lineage_id.in_(lineage_ids),
            Content.is_latest.is_(True),
            Content.version_no > 0,
        )
    ).all()
    latest_by_lineage = {str(lid): int(vid) for lid, vid in latest_rows}
    stale = 0
    for row in index_rows:
        lid = str(row[0])
        if lid not in workspace_lids:
            continue
        indexed_vid = int(row[1])
        latest_vid = latest_by_lineage.get(lid)
        if latest_vid is not None and latest_vid != indexed_vid:
            stale += 1
    return stale


@dataclass(frozen=True)
class RagIndexStatus:
    indexed_documents: int
    total_documents: int
    stale_documents: int
    orphan_documents: int
    active_chunks: int
    historical_chunks: int
    index_size_bytes: int | None
    last_indexed_at: float | None


def workspace_lineage_ids(session: Any) -> set[str]:
    """Lineage ids for every markdown file in the current workspace tree."""
    lids: set[str] = set()
    for path in iter_workspace_markdown_paths():
        lineage = content_repo.get_or_create_lineage(session, path)
        lids.add(str(lineage.lineage_id))
    return lids


def prune_orphan_rag_lineages(conn: Any, session: Any) -> int:
    """Remove RAG index rows for lineages no longer in the workspace tree."""
    ws_lids = workspace_lineage_ids(session)
    indexed_rows = conn.execute("SELECT lineage_id FROM rag_lineage_index").fetchall()
    pruned = 0
    for row in indexed_rows:
        lid = str(row[0])
        if lid not in ws_lids:
            store_db.rag_delete_for_lineage(conn, lid)
            pruned += 1
    if pruned:
        conn.commit()
    return pruned


def format_index_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "—"
    return _format_bytes(size_bytes)


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


def format_idle_status_line(status: RagIndexStatus) -> str:
    if status.indexed_documents == 0 and status.last_indexed_at is None:
        return "Not indexed"
    parts = [format_status_line(status)]
    ts = _format_timestamp(status.last_indexed_at)
    if ts != "—":
        parts.append(ts)
    return " · ".join(parts)


def format_chunks_line(status: RagIndexStatus) -> str:
    parts = [f"{status.active_chunks:,} active", f"{status.historical_chunks:,} historical"]
    if status.index_size_bytes is not None:
        parts.append(_format_bytes(status.index_size_bytes))
    return " · ".join(parts)


def compute_rag_index_status(conn: Any, session: Any) -> RagIndexStatus:
    ws_lids = workspace_lineage_ids(session)
    total_documents = len(ws_lids)
    indexed_in_db = {
        str(row[0])
        for row in conn.execute("SELECT lineage_id FROM rag_lineage_index").fetchall()
    }
    indexed_documents = len(ws_lids & indexed_in_db)
    orphan_documents = len(indexed_in_db - ws_lids)

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

    stale_documents = _count_stale_indexed_documents(conn, session, workspace_lids=ws_lids)

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
        orphan_documents=orphan_documents,
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


def rag_stat_values(status: RagIndexStatus) -> dict[str, str]:
    documents = f"{status.indexed_documents} / {status.total_documents} indexed"
    if status.stale_documents:
        documents += f" · {status.stale_documents} stale"
    return {
        "documents": documents,
        "index_size": format_index_size(status.index_size_bytes),
        "last_indexed": _format_timestamp(status.last_indexed_at),
        "active_chunks": f"{status.active_chunks:,}",
        "historical_chunks": f"{status.historical_chunks:,}",
    }
