#!/usr/bin/env python3
"""Smoke test: sqlite-vec + paragraph_embedding_cache round-trip."""

from __future__ import annotations

import sqlite3

import numpy as np

from iterthink.persistence.store_db import (
    embedding_cache_get,
    embedding_cache_put,
    init_schema,
    load_sqlite_vec_extension,
)


def main() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    load_sqlite_vec_extension(conn)
    init_schema(conn)
    doc = "/tmp/example.md"
    h = "a" * 64
    mid = "nomic-embed-text-v1.5-Q"
    vec = np.zeros(768, dtype=np.float32)
    vec[0] = 0.25
    vec[1] = -0.5
    embedding_cache_put(conn, doc, h, mid, vec)
    blob = embedding_cache_get(conn, doc, h, mid)
    assert blob is not None and len(blob) == 768 * 4, len(blob)
    conn.close()
    print("verify_embedding_cache: ok")


if __name__ == "__main__":
    main()
