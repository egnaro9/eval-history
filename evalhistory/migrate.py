"""Bringing a database up to head.

Two things make this less trivial than `alembic upgrade head`:

1. **The deployed database predates Alembic.** It was built by `create_all()`
   and holds real rows. Running the initial migration against it would try to
   CREATE TABLE over live tables and fail. It has to be *stamped* instead —
   told "you are already at this revision" — which is only honest because the
   schema it was created from is the same metadata the migration was generated
   from, and CI proves those two agree (see tests/test_migrations.py). Without
   that test this function would be lying to the database.

2. **SQLite tests don't want migration machinery.** The suite runs against a
   fresh in-memory database per test; `create_all()` is faster and the thing
   under test isn't the migration.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from .models import Base

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def ensure_schema(engine) -> str:
    """Make the database match the models. Returns what it did, for the log."""
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)
        return "created (sqlite)"

    from alembic import command

    tables = set(inspect(engine).get_table_names())
    cfg = _alembic_config()

    if "runs" in tables and "alembic_version" not in tables:
        # Adopting a database that already existed. Safe only because the
        # schema came from this same metadata; CI checks that migrations and
        # models haven't drifted apart.
        command.stamp(cfg, "head")
        return "stamped (adopted a pre-Alembic database)"

    command.upgrade(cfg, "head")
    return "upgraded"
