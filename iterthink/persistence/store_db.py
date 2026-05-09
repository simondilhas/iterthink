"""SQLite store for settings, paragraph observations, and sqlite-vec embedding cache."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Sequence

import numpy as np
import sqlite_vec

from iterthink import config

SCHEMA_VERSION = 3

SETTINGS_CHAT = "ollama_chat_model"
SETTINGS_EMBED = "ollama_embed_model"

SETTINGS_KI_TIER = "ki_tier"
SETTINGS_CLOUD_VENDOR = "cloud_vendor"
SETTINGS_COMPANY_OPENAI_MODEL = "company_openai_model"
SETTINGS_COMPANY_OPENAI_BASE_URL = "company_openai_base_url"
SETTINGS_CLOUD_ANTHROPIC_MODEL = "cloud_anthropic_model"
SETTINGS_CLOUD_OPENAI_MODEL = "cloud_openai_model"
SETTINGS_CLOUD_GOOGLE_MODEL = "cloud_google_model"
SETTINGS_EXPORT_AUTHOR = "export_author"


def ensure_store_dir() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)


def load_sqlite_vec_extension(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec into this connection (required before vec0 DDL or queries)."""
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_store_dir()
    path = db_path or config.STORE_DB_PATH
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    load_sqlite_vec_extension(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version")
    ver = int(cur.fetchone()[0])
    if ver >= SCHEMA_VERSION:
        return
    if ver < 1:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paragraph_text (
                hash TEXT PRIMARY KEY NOT NULL,
                text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paragraph_observation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_path TEXT NOT NULL,
                slot_index INTEGER NOT NULL,
                text_hash TEXT NOT NULL,
                embed_model TEXT NOT NULL,
                embedding BLOB NOT NULL,
                prev_text_hash TEXT,
                cosine_to_prev REAL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_obs_doc_slot_time
            ON paragraph_observation (doc_path, slot_index, created_at DESC);
            """
        )
    if ver < 2:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS paragraph_vec USING vec0(
                embedding float[768]
            );

            CREATE TABLE IF NOT EXISTS paragraph_embedding_cache (
                doc_path TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                embed_model_id TEXT NOT NULL,
                vec_rowid INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (doc_path, input_hash, embed_model_id)
            );

            CREATE INDEX IF NOT EXISTS idx_embed_cache_vec_rowid
            ON paragraph_embedding_cache(vec_rowid);
            """
        )
    if ver < 3:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_file_manifest (
                file_path TEXT NOT NULL,
                embed_model_id TEXT NOT NULL,
                file_mtime REAL NOT NULL,
                chunk_hashes TEXT NOT NULL,
                ingested_at REAL NOT NULL,
                PRIMARY KEY (file_path, embed_model_id)
            );
            """
        )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def embedding_cache_get(
    conn: sqlite3.Connection, doc_path: str, input_hash: str, embed_model_id: str
) -> bytes | None:
    row = conn.execute(
        """
        SELECT c.vec_rowid
        FROM paragraph_embedding_cache c
        WHERE c.doc_path = ? AND c.input_hash = ? AND c.embed_model_id = ?
        """,
        (doc_path, input_hash, embed_model_id),
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
    doc_path: str,
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
            doc_path, input_hash, embed_model_id, vec_rowid, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (doc_path, input_hash, embed_model_id, rid, now),
    )
    conn.commit()


def settings_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def settings_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def paragraph_text_upsert(conn: sqlite3.Connection, text_hash: str, text: str) -> None:
    conn.execute(
        "INSERT INTO paragraph_text(hash, text) VALUES(?, ?) ON CONFLICT(hash) DO NOTHING",
        (text_hash, text),
    )


def latest_observation(
    conn: sqlite3.Connection, doc_path: str, slot_index: int, embed_model: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, doc_path, slot_index, text_hash, embed_model, embedding, prev_text_hash,
               cosine_to_prev, status, created_at
        FROM paragraph_observation
        WHERE doc_path = ? AND slot_index = ? AND embed_model = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (doc_path, slot_index, embed_model),
    ).fetchone()


def insert_observation(
    conn: sqlite3.Connection,
    *,
    doc_path: str,
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
            doc_path, slot_index, text_hash, embed_model, embedding,
            prev_text_hash, cosine_to_prev, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_path,
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


# ---------------------------------------------------------------------------
# Project file manifest (for Impact RAG ingestion tracking)
# ---------------------------------------------------------------------------

def manifest_get(
    conn: sqlite3.Connection, file_path: str, embed_model_id: str
) -> dict | None:
    """Return manifest row for (file_path, embed_model_id), or None if not found."""
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
    """Upsert a manifest entry, recording the current mtime and chunk hash list."""
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
