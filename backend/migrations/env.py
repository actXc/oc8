"""Alembic environment. Runs migrations as the schema-owner (migration) role."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from oc8 import models  # noqa: F401  (registers all tables on Base.metadata)
from oc8.config import get_settings
from oc8.db.base import Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers defaults to TRUE, and alembic's own template
    # leaves it there. That switches off every logger already created -- which,
    # because `oc8.models` is imported four lines above, is every `oc8.*` logger
    # in the process. Harmless for the migrate container, which does nothing
    # else; not harmless for the test suite, which runs migrations once at
    # session start and then spends the rest of the run unable to see a single
    # warning any oc8 module writes. A warning nobody can observe is a warning
    # that cannot be tested, and this suite had two of those before anyone
    # noticed the cause was here.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().migration_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().migration_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
