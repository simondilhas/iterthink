"""Run Alembic migrations and ensure legacy sqlite tables exist."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from iterthink import config
from iterthink.persistence import store_db


def _alembic_script_dir() -> Path:
    """Migrations live under the iterthink package so pip installs run upgrades."""
    return Path(__file__).resolve().parents[1] / "alembic"


def run_alembic_upgrade() -> None:
    """Apply Alembic migrations to the store database."""
    script_dir = _alembic_script_dir()
    if not (script_dir / "env.py").is_file():
        return
    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir.resolve()))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{config.STORE_DB_PATH.resolve()}")
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
