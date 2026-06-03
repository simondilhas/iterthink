"""RAG SQLite store (``store.rag.sqlite3``): vectors, chunks, observations."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Sequence

import numpy as np
import sqlite_vec

from iterthink import config

RAG_SCHEMA_VERSION = 2

SETTINGS_CHAT = "ollama_chat_model"
SETTINGS_EMBED = "ollama_embed_model"
SETTINGS_RAG_ENRICHMENT_MODE = "rag_enrichment_mode"
SETTINGS_RAG_RERANKER_ENABLED = "rag_reranker_enabled"
SETTINGS_RAG_LATEST_VERSION_ONLY = "rag_latest_version_only"

SETTINGS_KI_TIER = "ki_tier"
SETTINGS_CLOUD_VENDOR = "cloud_vendor"
SETTINGS_COMPANY_OPENAI_MODEL = "company_openai_model"
SETTINGS_COMPANY_OPENAI_BASE_URL = "company_openai_base_url"
SETTINGS_CLOUD_ANTHROPIC_MODEL = "cloud_anthropic_model"
SETTINGS_CLOUD_OPENAI_MODEL = "cloud_openai_model"
SETTINGS_CLOUD_GOOGLE_MODEL = "cloud_google_model"
SETTINGS_EXPORT_AUTHOR = "export_author"
SETTINGS_SPELLCHECK_DICTIONARY_PATH = "spellcheck_dictionary_path"
SETTINGS_SPELLCHECK_LANGUAGE_MODE = "spellcheck_language_mode"
SETTINGS_SPELLCHECK_LANGUAGE = "spellcheck_language"
SETTINGS_PROMPTS_BUNDLED_DISMISSED = "prompts_bundled_dismissed"
SETTINGS_PROMPTS_REMOVED_IDS = "prompts_removed_ids"


def ensure_store_dir() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)


def load_sqlite_vec_extension(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_store_dir()
    path = db_path or config.RAG_DB_PATH
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    load_sqlite_vec_extension(conn)
    return conn


_RAG_SCHEMA_V1 = """
        CREATE TABLE IF NOT EXISTS paragraph_text (
            hash TEXT PRIMARY KEY NOT NULL,
            text TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paragraph_observation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lineage_id TEXT NOT NULL,
            slot_index INTEGER NOT NULL,
            text_hash TEXT NOT NULL,
            embed_model TEXT NOT NULL,
            embedding BLOB NOT NULL,
            prev_text_hash TEXT,
            cosine_to_prev REAL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_obs_lineage_slot_time
        ON paragraph_observation (lineage_id, slot_index, created_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS paragraph_vec USING vec0(
            embedding float[768]
        );

        CREATE TABLE IF NOT EXISTS paragraph_embedding_cache (
            lineage_id TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            embed_model_id TEXT NOT NULL,
            vec_rowid INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (lineage_id, input_hash, embed_model_id)
        );

        CREATE INDEX IF NOT EXISTS idx_embed_cache_vec_rowid
        ON paragraph_embedding_cache(vec_rowid);

        CREATE TABLE IF NOT EXISTS project_file_manifest (
            file_path TEXT NOT NULL,
            embed_model_id TEXT NOT NULL,
            file_mtime REAL NOT NULL,
            chunk_hashes TEXT NOT NULL,
            ingested_at REAL NOT NULL,
            PRIMARY KEY (file_path, embed_model_id)
        );

        CREATE TABLE IF NOT EXISTS impact_version_chunk (
            content_version_id INTEGER NOT NULL,
            lineage_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            input_hash TEXT NOT NULL,
            vec_rowid INTEGER NOT NULL,
            embed_model_id TEXT NOT NULL,
            chunk_text TEXT NOT NULL,
            content_sha TEXT NOT NULL,
            chunk_type TEXT NOT NULL DEFAULT 'unknown',
            created_at REAL NOT NULL,
            PRIMARY KEY (content_version_id, chunk_index)
        );

        CREATE INDEX IF NOT EXISTS idx_impact_version_chunk_lineage
        ON impact_version_chunk (lineage_id);

        CREATE INDEX IF NOT EXISTS idx_impact_version_chunk_ver
        ON impact_version_chunk (content_version_id);
"""

_RAG_SCHEMA_V2 = """
        CREATE TABLE IF NOT EXISTS rag_parent_chunk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lineage_id TEXT NOT NULL,
            content_version_id INTEGER NOT NULL,
            parent_index INTEGER NOT NULL,
            doc_title TEXT NOT NULL,
            section_header TEXT NOT NULL,
            parent_text TEXT NOT NULL,
            content_sha TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE (content_version_id, parent_index)
        );

        CREATE INDEX IF NOT EXISTS idx_rag_parent_lineage
        ON rag_parent_chunk (lineage_id);

        CREATE INDEX IF NOT EXISTS idx_rag_parent_ver
        ON rag_parent_chunk (content_version_id);

        CREATE TABLE IF NOT EXISTS rag_child_chunk (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id INTEGER NOT NULL,
            lineage_id TEXT NOT NULL,
            content_version_id INTEGER NOT NULL,
            slot_index INTEGER NOT NULL,
            raw_text TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            questions_json TEXT NOT NULL DEFAULT '[]',
            embed_text TEXT NOT NULL,
            overlap_text TEXT NOT NULL DEFAULT '',
            input_hash TEXT NOT NULL,
            vec_rowid INTEGER NOT NULL,
            embed_model_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (parent_id) REFERENCES rag_parent_chunk(id) ON DELETE CASCADE,
            UNIQUE (content_version_id, slot_index)
        );

        CREATE INDEX IF NOT EXISTS idx_rag_child_lineage
        ON rag_child_chunk (lineage_id);

        CREATE INDEX IF NOT EXISTS idx_rag_child_parent
        ON rag_child_chunk (parent_id);

        CREATE INDEX IF NOT EXISTS idx_rag_child_ver
        ON rag_child_chunk (content_version_id);

        CREATE TABLE IF NOT EXISTS rag_lineage_index (
            lineage_id TEXT PRIMARY KEY NOT NULL,
            content_version_id INTEGER NOT NULL,
            content_sha TEXT NOT NULL,
            indexed_at REAL NOT NULL,
            enrichment_mode TEXT NOT NULL DEFAULT 'skip'
        );
"""


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version")
    ver = int(cur.fetchone()[0])
    if ver >= RAG_SCHEMA_VERSION:
        return
    if ver < 1:
        conn.executescript(_RAG_SCHEMA_V1)
        ver = 1
    if ver < 2:
        conn.executescript(_RAG_SCHEMA_V2)
        ver = 2
    conn.execute(f"PRAGMA user_version = {RAG_SCHEMA_VERSION}")
    conn.commit()


def embedding_cache_get(
    conn: sqlite3.Connection, lineage_id: str, input_hash: str, embed_model_id: str
) -> bytes | None:
    row = conn.execute(
        """
        SELECT c.vec_rowid
        FROM paragraph_embedding_cache c
        WHERE c.lineage_id = ? AND c.input_hash = ? AND c.embed_model_id = ?
        """,
        (lineage_id, input_hash, embed_model_id),
    ).fetchone()
    if row is None:
        return None
    rid = int(row[0])
    emb = conn.execute(
        "SELECT embedding FROM paragraph_vec WHERE rowid = ?",
        (rid,),
    ).fetchone()
    if emb is None:
        return None
    return bytes(emb[0])


def embedding_cache_put(
    conn: sqlite3.Connection,
    lineage_id: str,
    input_hash: str,
    embed_model_id: str,
    vector: Sequence[float] | np.ndarray,
) -> None:
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    blob = sqlite_vec.serialize_float32(arr.tolist())
    now = time.time()
    conn.execute("INSERT INTO paragraph_vec(embedding) VALUES (?)", (blob,))
    rid_row = conn.execute("SELECT last_insert_rowid()").fetchone()
    rid = int(rid_row[0]) if rid_row is not None else 0
    conn.execute(
        """
        INSERT INTO paragraph_embedding_cache (
            lineage_id, input_hash, embed_model_id, vec_rowid, created_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(lineage_id, input_hash, embed_model_id) DO UPDATE SET
            vec_rowid = excluded.vec_rowid,
            created_at = excluded.created_at
        """,
        (lineage_id, input_hash, embed_model_id, rid, now),
    )
    conn.commit()


# Legacy aliases: callers passing doc_path use it as lineage_id key when path-only
def embedding_cache_get_by_doc_path(
    conn: sqlite3.Connection, doc_path: str, input_hash: str, embed_model_id: str
) -> bytes | None:
    return embedding_cache_get(conn, doc_path, input_hash, embed_model_id)


def embedding_cache_put_by_doc_path(
    conn: sqlite3.Connection,
    doc_path: str,
    input_hash: str,
    embed_model_id: str,
    vector: Sequence[float] | np.ndarray,
) -> None:
    embedding_cache_put(conn, doc_path, input_hash, embed_model_id, vector)


def paragraph_text_upsert(conn: sqlite3.Connection, text_hash: str, text: str) -> None:
    conn.execute(
        "INSERT INTO paragraph_text(hash, text) VALUES(?, ?) ON CONFLICT(hash) DO NOTHING",
        (text_hash, text),
    )


def latest_observation(
    conn: sqlite3.Connection, lineage_id: str, slot_index: int, embed_model: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, lineage_id, slot_index, text_hash, embed_model, embedding, prev_text_hash,
               cosine_to_prev, status, created_at
        FROM paragraph_observation
        WHERE lineage_id = ? AND slot_index = ? AND embed_model = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (lineage_id, slot_index, embed_model),
    ).fetchone()


def insert_observation(
    conn: sqlite3.Connection,
    *,
    lineage_id: str,
    slot_index: int,
    text_hash: str,
    embed_model: str,
    embedding: bytes,
    prev_text_hash: str | None,
    cosine_to_prev: float | None,
    status: str,
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO paragraph_observation (
            lineage_id, slot_index, text_hash, embed_model, embedding,
            prev_text_hash, cosine_to_prev, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lineage_id,
            slot_index,
            text_hash,
            embed_model,
            embedding,
            prev_text_hash,
            cosine_to_prev,
            status,
            now,
        ),
    )
    conn.commit()


def manifest_get(conn: sqlite3.Connection, file_path: str, embed_model_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT file_path, embed_model_id, file_mtime, chunk_hashes, ingested_at
        FROM project_file_manifest
        WHERE file_path = ? AND embed_model_id = ?
        """,
        (file_path, embed_model_id),
    ).fetchone()
    return dict(row) if row is not None else None


def manifest_put(
    conn: sqlite3.Connection,
    file_path: str,
    embed_model_id: str,
    file_mtime: float,
    chunk_hashes: list[str],
) -> None:
    now = time.time()
    hashes_json = json.dumps(chunk_hashes)
    conn.execute(
        """
        INSERT INTO project_file_manifest (
            file_path, embed_model_id, file_mtime, chunk_hashes, ingested_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(file_path, embed_model_id) DO UPDATE SET
            file_mtime = excluded.file_mtime,
            chunk_hashes = excluded.chunk_hashes,
            ingested_at = excluded.ingested_at
        """,
        (file_path, embed_model_id, file_mtime, hashes_json, now),
    )
    conn.commit()


def impact_version_chunk_delete_for_version(conn: sqlite3.Connection, content_version_id: int) -> None:
    conn.execute(
        "DELETE FROM impact_version_chunk WHERE content_version_id = ?",
        (int(content_version_id),),
    )
    conn.commit()


def impact_version_chunk_delete_for_lineage(conn: sqlite3.Connection, lineage_id: str) -> None:
    conn.execute("DELETE FROM impact_version_chunk WHERE lineage_id = ?", (lineage_id,))
    conn.commit()


def impact_version_chunk_delete_for_document(conn: sqlite3.Connection, doc_id: int) -> None:
    """Legacy name: ``doc_id`` is ``content_version_id`` or latest version id."""
    impact_version_chunk_delete_for_version(conn, int(doc_id))


def impact_version_chunk_insert_row(
    conn: sqlite3.Connection,
    *,
    content_version_id: int,
    lineage_id: str,
    chunk_index: int,
    input_hash: str,
    vec_rowid: int,
    embed_model_id: str,
    chunk_text: str,
    content_sha: str,
    chunk_type: str = "unknown",
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO impact_version_chunk (
            content_version_id, lineage_id, chunk_index, input_hash, vec_rowid, embed_model_id,
            chunk_text, content_sha, chunk_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content_version_id,
            lineage_id,
            chunk_index,
            input_hash,
            vec_rowid,
            embed_model_id,
            chunk_text,
            content_sha,
            chunk_type,
            now,
        ),
    )


def impact_version_chunk_fetch_latest_rows(
    conn: sqlite3.Connection, lineage_ids: list[str]
) -> list[tuple[str, int, int, int, str, str]]:
    if not lineage_ids:
        return []
    placeholders = ",".join("?" * len(lineage_ids))
    sql = f"""
    SELECT c.lineage_id, c.content_version_id, c.chunk_index, c.vec_rowid, c.chunk_text, c.chunk_type
    FROM impact_version_chunk c
    INNER JOIN (
        SELECT lineage_id AS lid, MAX(content_version_id) AS mv
        FROM impact_version_chunk
        WHERE lineage_id IN ({placeholders})
        GROUP BY lineage_id
    ) t ON c.lineage_id = t.lid AND c.content_version_id = t.mv
    ORDER BY c.lineage_id, c.chunk_index
    """
    rows = conn.execute(sql, lineage_ids).fetchall()
    return [(str(r[0]), int(r[1]), int(r[2]), int(r[3]), str(r[4]), str(r[5])) for r in rows]


def impact_version_embeddings_complete(
    conn: sqlite3.Connection,
    content_version_id: int,
    content_sha: str,
    chunk_count: int,
) -> bool:
    if chunk_count == 0:
        return False
    row = conn.execute(
        """
        SELECT COUNT(*), MAX(content_sha) FROM impact_version_chunk
        WHERE content_version_id = ?
        """,
        (content_version_id,),
    ).fetchone()
    if row is None:
        return False
    cnt, sha = int(row[0]), str(row[1] or "")
    return cnt == chunk_count and sha == content_sha


# ---------------------------------------------------------------------------
# Workspace RAG (parent/child chunks)
# ---------------------------------------------------------------------------


def rag_delete_for_lineage(conn: sqlite3.Connection, lineage_id: str) -> None:
    conn.execute("DELETE FROM rag_child_chunk WHERE lineage_id = ?", (lineage_id,))
    conn.execute("DELETE FROM rag_parent_chunk WHERE lineage_id = ?", (lineage_id,))
    conn.execute("DELETE FROM rag_lineage_index WHERE lineage_id = ?", (lineage_id,))
    conn.commit()


def rag_delete_for_version(conn: sqlite3.Connection, content_version_id: int) -> None:
    conn.execute("DELETE FROM rag_child_chunk WHERE content_version_id = ?", (int(content_version_id),))
    conn.execute("DELETE FROM rag_parent_chunk WHERE content_version_id = ?", (int(content_version_id),))
    conn.commit()


def rag_lineage_index_get(conn: sqlite3.Connection, lineage_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT lineage_id, content_version_id, content_sha, indexed_at, enrichment_mode
        FROM rag_lineage_index WHERE lineage_id = ?
        """,
        (lineage_id,),
    ).fetchone()


def rag_lineage_index_put(
    conn: sqlite3.Connection,
    *,
    lineage_id: str,
    content_version_id: int,
    content_sha: str,
    enrichment_mode: str,
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO rag_lineage_index (
            lineage_id, content_version_id, content_sha, indexed_at, enrichment_mode
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(lineage_id) DO UPDATE SET
            content_version_id = excluded.content_version_id,
            content_sha = excluded.content_sha,
            indexed_at = excluded.indexed_at,
            enrichment_mode = excluded.enrichment_mode
        """,
        (lineage_id, int(content_version_id), content_sha, now, enrichment_mode),
    )


def rag_parent_insert(
    conn: sqlite3.Connection,
    *,
    lineage_id: str,
    content_version_id: int,
    parent_index: int,
    doc_title: str,
    section_header: str,
    parent_text: str,
    content_sha: str,
) -> int:
    now = time.time()
    conn.execute(
        """
        INSERT INTO rag_parent_chunk (
            lineage_id, content_version_id, parent_index, doc_title, section_header,
            parent_text, content_sha, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lineage_id,
            int(content_version_id),
            int(parent_index),
            doc_title,
            section_header,
            parent_text,
            content_sha,
            now,
        ),
    )
    row = conn.execute("SELECT last_insert_rowid()").fetchone()
    return int(row[0]) if row is not None else 0


def rag_child_insert(
    conn: sqlite3.Connection,
    *,
    parent_id: int,
    lineage_id: str,
    content_version_id: int,
    slot_index: int,
    raw_text: str,
    summary: str,
    questions_json: str,
    embed_text: str,
    overlap_text: str,
    input_hash: str,
    vec_rowid: int,
    embed_model_id: str,
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO rag_child_chunk (
            parent_id, lineage_id, content_version_id, slot_index, raw_text, summary,
            questions_json, embed_text, overlap_text, input_hash, vec_rowid, embed_model_id,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(parent_id),
            lineage_id,
            int(content_version_id),
            int(slot_index),
            raw_text,
            summary,
            questions_json,
            embed_text,
            overlap_text,
            input_hash,
            int(vec_rowid),
            embed_model_id,
            now,
        ),
    )


def rag_child_fetch_latest_rows(
    conn: sqlite3.Connection, lineage_ids: list[str] | None = None
) -> list[tuple]:
    """Return child rows for latest indexed version per lineage.

    Tuple: (child_id, lineage_id, content_version_id, slot_index, raw_text, summary,
    questions_json, embed_text, vec_rowid, parent_id, doc_title, section_header, parent_text)
    """
    if lineage_ids is not None and not lineage_ids:
        return []
    base = """
    SELECT c.id, c.lineage_id, c.content_version_id, c.slot_index, c.raw_text, c.summary,
           c.questions_json, c.embed_text, c.vec_rowid, c.parent_id,
           p.doc_title, p.section_header, p.parent_text
    FROM rag_child_chunk c
    INNER JOIN rag_parent_chunk p ON p.id = c.parent_id
    INNER JOIN rag_lineage_index li ON li.lineage_id = c.lineage_id
        AND li.content_version_id = c.content_version_id
    """
    if lineage_ids is not None:
        placeholders = ",".join("?" * len(lineage_ids))
        sql = base + f" WHERE c.lineage_id IN ({placeholders}) ORDER BY c.lineage_id, c.slot_index"
        rows = conn.execute(sql, lineage_ids).fetchall()
    else:
        sql = base + " ORDER BY c.lineage_id, c.slot_index"
        rows = conn.execute(sql).fetchall()
    return [tuple(r) for r in rows]


def rag_child_fetch_by_version(
    conn: sqlite3.Connection, content_version_id: int
) -> list[tuple]:
    rows = conn.execute(
        """
        SELECT c.id, c.lineage_id, c.content_version_id, c.slot_index, c.raw_text, c.summary,
               c.questions_json, c.embed_text, c.vec_rowid, c.parent_id,
               p.doc_title, p.section_header, p.parent_text
        FROM rag_child_chunk c
        INNER JOIN rag_parent_chunk p ON p.id = c.parent_id
        WHERE c.content_version_id = ?
        ORDER BY c.slot_index
        """,
        (int(content_version_id),),
    ).fetchall()
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# Settings: delegate to entity DB via session passed by caller
# ---------------------------------------------------------------------------


def settings_get(conn: sqlite3.Connection, key: str) -> str | None:
    """Deprecated on RAG conn — use entity_settings with SQLAlchemy session."""
    from iterthink.db.session import session_scope
    from iterthink.persistence import entity_settings

    with session_scope() as session:
        return entity_settings.settings_get(session, key)


def settings_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    from iterthink.db.session import session_scope
    from iterthink.persistence import entity_settings

    with session_scope() as session:
        entity_settings.settings_set(session, key, value)
