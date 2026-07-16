# eval-history

[![ci](https://github.com/egnaro9/eval-history/actions/workflows/ci.yml/badge.svg)](https://github.com/egnaro9/eval-history/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![Postgres](https://img.shields.io/badge/Postgres-16-336791)](https://www.postgresql.org/)
[![tests](https://img.shields.io/badge/tests-30-brightgreen)](tests)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Store eval runs in Postgres and find out what got worse.**

An eval score tells you how the system did *today*. It can't tell you that yesterday's change made things worse — and "worse" is the only thing you actually need to be told. This is the missing half: a REST API that ingests [rag-eval-lab](https://github.com/egnaro9/rag-eval-lab)'s `eval_run.json`, keeps every run, and answers one question well:

```
GET /runs/{baseline}/compare/{candidate}   →   { "verdict": "regressed", ... }
```

```
rag-eval-lab  ──eval_run.json──►  eval-history  ──compare──►  "regressed"
 (produces)                        (remembers)                 (tells you)
```

---

## What it does

| | |
| --- | --- |
| `POST /runs` | Store a run — **accepts `eval_run.json` verbatim**, no translation |
| `GET /runs` | List, newest first; filter by suite, paginated |
| `GET /runs/{id}` | One run with every case |
| `GET /runs/{a}/compare/{b}` | **What changed** — per-case regressions, improvements, newly-flagged |
| `GET /suites/{name}/latest-comparison` | The last two runs of a suite — the CI shortcut |
| `DELETE /runs/{id}` | Write key required |

Writes need a `Bearer` key; **reads are open**. That asymmetry is deliberate: anyone can look, nobody can scribble.

## The interesting part is the comparison

Storing runs is easy. Saying something *useful* about two of them is the actual work, and it's all in [`compare.py`](evalhistory/compare.py) — pure, no database, no framework, so the thing worth being correct is trivially testable.

Three decisions in there worth defending:

- **A tolerance band.** Float scores wobble in the last decimal. Without a band, every run "regresses" and the signal drowns in noise.
- **Newly-flagged outranks a metric dip.** A case crossing the hallucination threshold is a *behaviour change*, not a rounding error — so it decides the verdict even when the numbers look flat.
- **Cases match on question text, not position or id.** Ids aren't stable across runs, and a reordered suite isn't a changed one. Questions that appear or vanish are reported **separately** rather than silently scored — a vanished case is a change to the *suite*, not evidence about the system.

## Run it

```bash
git clone https://github.com/egnaro9/eval-history && cd eval-history
pip install -e ".[dev]"
pytest -q                    # 30 tests, no database required
uvicorn evalhistory.app:app --reload
```

```bash
# store a run straight out of rag-eval-lab
python -m ragevallab.cli eval --out eval_run.json
curl -X POST localhost:8000/runs -H "Authorization: Bearer dev-key" \
     -H "content-type: application/json" -d @eval_run.json

# then, after a change:
curl "localhost:8000/suites/rag-eval-lab/latest-comparison" | jq .verdict
# "regressed"
```

Interactive OpenAPI docs at `/docs`.

## The database

Two tables, one relationship: a run has many cases. [`models.py`](evalhistory/models.py).

- **Metrics are columns, not a JSON blob.** They're what every query sorts, filters and compares on — so they get types, indexes and `NOT NULL`. The genuinely shapeless bits (which chunk ids came back) stay JSON, because indexing them would buy nothing.
- **Aggregates are denormalised onto `runs`.** The list view sorts on them; recomputing per row would mean a join and an aggregate to render twenty rows.
- **Indexes match the actual queries** — newest-first listing, runs-of-a-suite, flagged-cases-in-a-run. Not one per column.
- `ON DELETE CASCADE` on the FK, so deleting a run can't leak orphaned cases.

**Postgres in production, SQLite in tests** — same models, same queries, same constraints. That keeps the suite runnable with no database installed, and **CI runs the entire suite a second time against a real Postgres 16 service container**, because SQLite parity is an assumption and the Postgres run is the check. It also asserts the FK and the indexes exist *at the database level*, not just in the ORM.

> One bug that caught: SQLite ships with foreign keys **off**. The cascade test passed on the ORM's cascade while Postgres would have been enforcing the real constraint — green locally, and a genuinely untested constraint in production. `db.py` now turns the pragma on so the test database enforces what Postgres enforces.

## Deploy

`render.yaml` is a Render blueprint — web service, health check, generated write key. Point `DATABASE_URL` at any Postgres.

**Two honest notes about free hosting**, because anyone clicking a live link deserves them:

- The **database is deliberately not declared in the blueprint.** Render's free Postgres is *deleted after 30 days* — a portfolio link that dies in a month is worse than no link. So the database lives on [Neon](https://neon.tech)'s free tier (permanent) and Render just holds the connection string.
- The **web service sleeps** after ~15 minutes idle and takes **~30s to wake**. That's the cost of free, and it's better said out loud than hidden behind a mystery spinner.

```bash
# any Postgres works — Neon, Supabase, RDS, or local
export DATABASE_URL="postgresql://user:pass@host/db"
uvicorn evalhistory.app:app
```

## Layout

```
evalhistory/
  compare.py   regression detection — pure, no I/O, the actual product
  models.py    SQLAlchemy schema: runs ──< cases, indexes, cascade
  db.py        engine/session; Postgres or SQLite; url normalisation
  schemas.py   Pydantic contract — accepts eval_run.json verbatim
  app.py       FastAPI: routes, auth, CORS, lifespan
tests/         30 tests — comparison logic, API, auth, validation, cascade
```

---

Built by [Erik Hill](https://egnaro9.github.io) · MIT · the other half of [rag-eval-lab](https://github.com/egnaro9/rag-eval-lab) and [eval-dashboard](https://github.com/egnaro9/eval-dashboard).
