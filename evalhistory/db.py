"""Engine + session wiring.

Postgres in production, SQLite in tests. That split is the same discipline as
the rest of this portfolio: the suite runs green with no database installed and
no network, while the deployed thing talks to a real Postgres. SQLAlchemy's
Core is what makes the two interchangeable — the models, queries and
constraints are identical either way.

Set DATABASE_URL to point at Postgres:
    postgresql+psycopg://user:pass@host/db
"""
from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

DEFAULT_URL = "sqlite+pysqlite:///:memory:"


def normalize_url(url: str) -> str:
    """Render (and most hosts) hand out `postgres://`, which SQLAlchemy 2 rejects.

    Rewriting it here rather than asking every deploy to get the scheme right is
    the difference between a five-minute deploy and a confusing 500.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def make_engine(url: str | None = None):
    url = normalize_url(url or os.environ.get("DATABASE_URL", DEFAULT_URL))
    kwargs: dict = {"future": True, "echo": bool(os.environ.get("SQL_ECHO"))}
    if url.startswith("sqlite"):
        # An in-memory SQLite DB is per-connection; without a shared pool each
        # session would get its own empty database.
        from sqlalchemy.pool import StaticPool

        kwargs.update(connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        # A free-tier Postgres has a small connection cap, and a sleeping
        # instance drops sockets — recycle and pre-ping rather than serving 500s.
        kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True, pool_recycle=300)
    engine = create_engine(url, **kwargs)

    if not url.startswith("sqlite"):
        # Force IPv4.
        #
        # Managed Postgres hostnames (Neon, Supabase, …) publish AAAA records,
        # and plenty of hosts — Render's free tier among them — have no IPv6
        # egress at all. libpq picks the AAAA, the packet has nowhere to go, and
        # you get "Network is unreachable" from an address that looks fine.
        #
        # Resolving the A record and passing `hostaddr` makes libpq dial IPv4
        # while `host` still carries the name for TLS/SNI. Done per-connection
        # rather than once at startup, because a managed provider's IP can move
        # under you and a cached one would fail on the next reconnect.
        @event.listens_for(engine, "do_connect")
        def _prefer_ipv4(dialect, conn_rec, cargs, cparams):  # pragma: no cover - needs a real host
            host = cparams.get("host")
            if not host or cparams.get("hostaddr"):
                return
            try:
                info = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            except socket.gaierror:
                return          # no A record — let libpq try whatever it finds
            if info:
                cparams["hostaddr"] = info[0][4][0]

    if url.startswith("sqlite"):
        # SQLite ships with foreign keys OFF. Without this the test database
        # silently ignores ON DELETE CASCADE while Postgres enforces it — the
        # tests would pass on a constraint production actually relies on.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _rec):  # pragma: no cover - wiring
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


ENGINE = make_engine()
SessionLocal = sessionmaker(bind=ENGINE, expire_on_commit=False, class_=Session)


def init_db(engine=None) -> None:
    Base.metadata.create_all(engine or ENGINE)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
