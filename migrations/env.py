"""Alembic environment.

The URL and the metadata both come from the application, never from
alembic.ini. A migration that reads its target from somewhere other than the
app does is a migration that can be pointed at the wrong database — and the
entire value of migrations is that they ran against the database the app uses.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from evalhistory.db import DEFAULT_URL, normalize_url
from evalhistory.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would switch off the
    # app's own "evalhistory" logger the moment migrations run — and migrations
    # run at startup on Postgres, so the structured request log would go silent
    # in production. Keep the existing loggers alive.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The same normalisation the app applies: postgres:// -> postgresql+psycopg://.
# The % escaping is for configparser, which reads a bare % as interpolation and
# would choke on a password that happens to contain one.
config.set_main_option(
    "sqlalchemy.url",
    normalize_url(os.environ.get("DATABASE_URL", DEFAULT_URL)).replace("%", "%%"),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Without compare_type, a column whose type changes autogenerates an
            # empty migration — worse than no migration, because it looks done.
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
