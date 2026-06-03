"""Run Alembic migrations and ensure legacy sqlite tables exist."""

from __future__ import annotations

import warnings
from pathlib import Path

from alembic import command
from alembic.config import Config

from iterthink import config
from iterthink.persistence import store_db
from iterthink.db.session import reset_engine_cache

# When adding a new Alembic revision, set this to the new head (only used if
# migration scripts are missing from the install — see _ensure_orm_schema).
ALEMBIC_HEAD_REVISION = "20260517_0015"


def _alembic_script_dir() -> Path:
    """Migrations live under the iterthink package so pip installs run upgrades."""
    return Path(__file__).resolve().parents[1] / "alembic"


def _alembic_config() -> Config | None:
    script_dir = _alembic_script_dir()
    if not (script_dir / "env.py").is_file():
        return None
    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir.resolve()))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{config.STORE_DB_PATH.resolve()}")
    return cfg


def run_alembic_upgrade() -> None:
    """Apply Alembic migrations to the store database."""
    cfg = _alembic_config()
    if cfg is None:
        return
    command.upgrade(cfg, "head")


def _ensure_orm_schema() -> None:
    """Guarantee ORM tables exist after upgrade (covers broken/missing packaged migrations)."""
    from sqlalchemy import inspect, text

    from iterthink.db.base import Base
    from iterthink.db import models  # noqa: F401 - register models on Base.metadata
    from iterthink.db.session import get_engine

    engine = get_engine()
    insp = inspect(engine)
    if insp.has_table("credential_vault"):
        return

    warnings.warn(
        "iterthink store is missing ORM tables after Alembic upgrade; "
        "creating them from SQLAlchemy metadata. Reinstall iterthink if this persists.",
        UserWarning,
        stacklevel=2,
    )
    Base.metadata.create_all(bind=engine)

    cfg = _alembic_config()
    if cfg is not None:
        command.upgrade(cfg, "head")
        return

    with engine.begin() as conn:
        has_av = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'")
        ).fetchone()
        if not has_av:
            conn.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
        if conn.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar_one() == 0:
            conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": ALEMBIC_HEAD_REVISION},
            )


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
    _ensure_orm_schema()
    reset_engine_cache()
