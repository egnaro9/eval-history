"""What the API actually asks the database.

`README.md` claims aggregates are denormalised onto `runs` so the list view
doesn't join. That claim was false in the way that matters: the relationship was
`lazy="selectin"`, so listing 20 runs also fetched every case belonging to them
— hundreds of rows, loaded and discarded, to render numbers already sitting in
the row. The comment explaining the optimisation was directly above the line
undoing it.

A sentence in a README can't notice that. This can. The site says "correctness
it can prove — not vibe-check"; a README bullet *is* a vibe-check.
"""
from __future__ import annotations

import pytest
from sqlalchemy import event

from evalhistory.db import ENGINE


@pytest.fixture
def sql():
    """Every statement the engine issues inside the block."""
    seen: list[str] = []

    def before(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(ENGINE, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(ENGINE, "before_cursor_execute", before)


def _selects(sql: list[str]) -> list[str]:
    return [s for s in sql if s.lstrip().upper().startswith("SELECT")]


def _touches_cases_table(statement: str) -> bool:
    """Does this statement read the cases TABLE?

    Not `"cases" in statement`: `runs` has columns named `flagged_cases` and
    `n_cases`, so a substring check calls every list query a case load. Match
    the table position instead.
    """
    s = " ".join(statement.lower().split())
    return " from cases" in s or " join cases" in s


def test_list_runs_does_not_load_cases(client, auth, make_run, sql):
    """Listing runs must not touch the cases table at all."""
    for i in range(5):
        r = client.post("/runs", json=make_run(name=f"s{i}"), headers=auth)
        assert r.status_code == 201

    sql.clear()
    assert client.get("/runs").status_code == 200

    selects = _selects(sql)
    assert len(selects) == 1, f"expected one SELECT to list runs, got {len(selects)}:\n" + "\n".join(selects)
    assert not _touches_cases_table(selects[0]), (
        "the list view read the cases table — the aggregates are denormalised "
        "onto runs so that it doesn't have to:\n" + selects[0]
    )


def test_getting_one_run_still_loads_its_cases(client, auth, make_run, sql):
    """The other half: dropping the eager load must not break the detail view."""
    created = client.post("/runs", json=make_run(), headers=auth).json()

    sql.clear()
    body = client.get(f"/runs/{created['id']}").json()

    assert len(body["cases"]) == 2
    assert any(_touches_cases_table(s) for s in _selects(sql)), "cases never loaded"
