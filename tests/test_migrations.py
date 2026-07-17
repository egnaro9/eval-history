"""Migrations must produce exactly what the models describe.

This is the test that makes the rest of the migration story true. Two claims
depend on it:

- `migrate.ensure_schema` *stamps* the deployed database rather than migrating
  it, because that database was built by `create_all()` from these models. That
  is only defensible if migrating from scratch would have produced the same
  schema. If these drift, the stamp becomes a lie told to a live database.
- Any future "I added a column" is only real if a migration carries it. Without
  this check, a model change with no migration passes CI and then 500s in
  production on a column Postgres has never heard of.

Postgres only, and skipped without DATABASE_URL: SQLite is relaxed about
exactly the things (types, constraints) this is here to compare.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect, text

from evalhistory.db import normalize_url
from evalhistory.models import Base

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").startswith(("postgres", "postgresql")),
    reason="needs a real Postgres — SQLite can't show type/constraint drift",
)


def _fresh_db(url: str, name: str):
    """A throwaway database, so neither path sees the other's leftovers."""
    admin = create_engine(normalize_url(url), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    base, _, _ = normalize_url(url).rpartition("/")
    return create_engine(f"{base}/{name}")


def _schema(engine) -> dict:
    insp = inspect(engine)
    out = {}
    for table in sorted(t for t in insp.get_table_names() if t != "alembic_version"):
        out[table] = {
            "columns": {
                c["name"]: (str(c["type"]).upper(), bool(c["nullable"]))
                for c in insp.get_columns(table)
            },
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                for i in insp.get_indexes(table)
            ),
            "pk": tuple(insp.get_pk_constraint(table)["constrained_columns"]),
            "fks": sorted(
                (tuple(f["constrained_columns"]), f["referred_table"],
                 tuple(f["referred_columns"]), (f.get("options") or {}).get("ondelete"))
                for f in insp.get_foreign_keys(table)
            ),
        }
    return out


def test_migrations_produce_exactly_what_the_models_describe():
    from alembic import command

    from evalhistory.migrate import _alembic_config

    url = os.environ["DATABASE_URL"]

    # Path A: what a fresh deploy gets, by running every migration.
    migrated = _fresh_db(url, "evalhistory_migrated")
    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", str(migrated.url.render_as_string(hide_password=False)).replace("%", "%%"))
    command.upgrade(cfg, "head")

    # Path B: what the models say, straight from metadata.
    modelled = _fresh_db(url, "evalhistory_modelled")
    Base.metadata.create_all(modelled)

    a, b = _schema(migrated), _schema(modelled)
    migrated.dispose()
    modelled.dispose()

    assert a == b, (
        "migrations and models have drifted.\n"
        f"migrated: {a}\n\nmodelled: {b}\n\n"
        "Run: alembic revision --autogenerate -m '<what changed>'"
    )
