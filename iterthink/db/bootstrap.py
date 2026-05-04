"""Run Alembic migrations and ensure legacy sqlite tables exist."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from iterthink import config, store_db


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _alembic_ini_path() -> Path:
    return _repo_root() / "alembic.ini"


def run_alembic_upgrade() -> None:
    """Apply Alembic migrations to the store database."""
    ini_path = _alembic_ini_path()
    if not ini_path.is_file():
        return
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{config.STORE_DB_PATH.resolve()}")
    cfg.set_main_option("script_location", str(_repo_root() / "alembic"))
    command.upgrade(cfg, "head")


def bootstrap_database() -> None:
    """
    Ensure store dir exists, legacy schema from store_db is present,
    then run Alembic for ORM-managed tables.
    """
    config.STORE_DIR.mkdir(parents=True, exist_ok=True)
    conn = store_db.connect()
    try:
        store_db.init_schema(conn)
    finally:
        conn.close()
    run_alembic_upgrade()
