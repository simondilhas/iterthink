"""SQLite store for settings and paragraph observations (embeddings, status)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from iterthink import config

SCHEMA_VERSION = 1

SETTINGS_CHAT = "ollama_chat_model"
SETTINGS_EMBED = "ollama_embed_model"


def ensure_store_dir() -> None:
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_store_dir()
    path = db_path or config.STORE_DB_PATH
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA user_version")
    ver = int(cur.fetchone()[0])
    if ver >= SCHEMA_VERSION:
        return
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
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
