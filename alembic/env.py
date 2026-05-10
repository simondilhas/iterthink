import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ensure repo root is importable when running `alembic` from any cwd
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from iterthink import config  # noqa: E402
from iterthink.db.base import Base  # noqa: E402
from iterthink.db import models  # noqa: F401, E402  # registers tables on Base.metadata

config_obj = context.config
if config_obj.config_file_name is not None:
    fileConfig(config_obj.config_file_name)

target_metadata = Base.metadata


_PLACEHOLDER_DB_URL = "sqlite:///placeholder.db"


def get_url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    if x.get("dburl"):
        return x["dburl"]
    ini_url = config_obj.get_main_option("sqlalchemy.url")
    if ini_url and ini_url != _PLACEHOLDER_DB_URL:
        return ini_url
    config.refresh()
    return f"sqlite:///{config.STORE_DB_PATH.resolve()}"


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config_obj.get_section(config_obj.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
