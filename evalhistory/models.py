"""The relational schema.

Two tables, one relationship. A run has many cases; a case belongs to one run.

The metrics live in columns rather than a JSON blob on purpose: they're what
every query sorts, filters and compares on, so they get types, indexes and NOT
NULL. The genuinely shapeless bits — which chunk ids came back — stay JSON,
because indexing them would buy nothing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, JSON, func
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Run(Base):
    """One execution of an eval suite."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    # Optional provenance — what produced this run. Nullable because a run
    # posted from a laptop legitimately has neither.
    # Why a run exists, not just what it scored.
    #
    # "ci"       — produced by a pipeline from a commit. Comparable to another.
    # "ablation" — a deliberate config sweep (retrieval k=3 vs k=2). Real data,
    #              but comparing it to a CI run attributes a config change to a
    #              commit and reports a regression nobody caused.
    #
    # latest-comparison filters to 'ci' for exactly that reason: "what did this
    # push break?" is a question only same-config runs can answer.
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ci")

    git_sha: Mapped[Optional[str]] = mapped_column(String(40))
    label: Mapped[Optional[str]] = mapped_column(String(200))

    # Aggregates. Denormalised from cases deliberately: the list view sorts and
    # filters on these, and recomputing them per row would mean a join and an
    # aggregate for a page that shows twenty runs.
    faithfulness: Mapped[float] = mapped_column(Float, nullable=False)
    precision_at_k: Mapped[float] = mapped_column(Float, nullable=False)
    recall_at_k: Mapped[float] = mapped_column(Float, nullable=False)
    citation_rate: Mapped[float] = mapped_column(Float, nullable=False)
    flagged_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    n_cases: Mapped[int] = mapped_column(Integer, nullable=False)

    cases: Mapped[List["Case"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        # Default lazy loading, not selectin. selectin eagerly fetched every
        # case for every run in a list query — 20 summaries dragging hundreds of
        # case rows across the wire to render aggregates that are already
        # denormalised onto this table precisely so they wouldn't have to.
        # The detail routes touch .cases and load them on access; the list route
        # never does. test_list_runs_does_not_load_cases holds this.
        # Without this the database returns cases in whatever order it likes,
        # and a suite read back is not the suite that was stored. Ids are random
        # uuids, so there is nothing else to order by — the position has to be
        # recorded when it's still known.
        order_by="Case.ordinal",
    )

    __table_args__ = (
        # The list view is always "newest first".
        Index("ix_runs_created_at", created_at.desc()),
        # "show me every run of this suite" — the compare flow starts here.
        Index("ix_runs_name_created", "name", created_at.desc()),
    )


class Case(Base):
    """One question inside a run."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )

    # Position within the run, as submitted. A suite has an order its author
    # chose, and "read it back" has to mean the same list, not the same set.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    q: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")

    # Shapeless, never queried on — JSON is the honest choice.
    retrieved: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)

    faithfulness: Mapped[float] = mapped_column(Float, nullable=False)
    precision_at_k: Mapped[float] = mapped_column(Float, nullable=False)
    recall_at_k: Mapped[float] = mapped_column(Float, nullable=False)
    citation: Mapped[float] = mapped_column(Float, nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    run: Mapped[Run] = relationship(back_populates="cases")

    __table_args__ = (
        Index("ix_cases_run_id", "run_id"),
        # "which cases are flagged in this run" — the first question anyone asks.
        Index("ix_cases_run_flagged", "run_id", "flagged"),
    )
