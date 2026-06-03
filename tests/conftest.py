"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from iterthink import config
from iterthink.db import bootstrap
from iterthink.db import session as db_session

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRATCH_ROOT = _REPO_ROOT / ".pytest_store"


@pytest.fixture
def ephemeral_store(monkeypatch: pytest.MonkeyPatch):
    """Isolated STORE_DIR + SQLite with Alembic schema (paragraph_analysis, etc.)."""
    _SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    store_dir = _SCRATCH_ROOT / f"iterthink_{uuid.uuid4().hex}"
    store_dir.mkdir(parents=True, exist_ok=True)
    db_path = store_dir / "store.sqlite3"
    rag_path = store_dir / "store.rag.sqlite3"
    monkeypatch.setattr(config, "STORE_DIR", store_dir)
    monkeypatch.setattr(config, "STORE_DB_PATH", db_path)
    monkeypatch.setattr(config, "RAG_DB_PATH", rag_path)
    db_session.reset_engine_cache()
    bootstrap.bootstrap_database()
    yield
    db_session.reset_engine_cache()
    shutil.rmtree(store_dir, ignore_errors=True)
