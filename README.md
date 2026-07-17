# eval-history

[![ci](https://github.com/egnaro9/eval-history/actions/workflows/ci.yml/badge.svg)](https://github.com/egnaro9/eval-history/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![Postgres](https://img.shields.io/badge/Postgres-16%20%7C%2018-336791)](https://www.postgresql.org/)
[![tests](https://img.shields.io/badge/tests-43-brightgreen)](tests)
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
| `GET /runs/{id}/eval_run` | The same run **in the shape it arrived in** — consumers that speak `eval_run.json` need no adapter |
| `GET /runs/{a}/compare/{b}` | **What changed** — per-case regressions, improvements, newly-flagged |
| `GET /suites/{name}/latest-comparison` | The last two **CI** runs of a suite — the CI shortcut. Ablations excluded: comparing a config sweep to a commit blames a regression on whoever pushed |
| `DELETE /runs/{id}` | Write key required |

Writes need a `Bearer` key; **reads are open**. That asymmetry is deliberate: anyone can look, nobody can scribble.

## The interesting part is the comparison

Storing runs is easy. Saying something *useful* about two of them is the actual work, and it's all in [`compare.py`](evalhistory/compare.py) — pure, no database, no framework, so the thing worth being correct is trivially testable.

Three decisions in there worth defending:

- **A tolerance band.** Float scores wobble in the last decimal. Without a band, every run "regresses" and the signal drowns in noise.
- **Newly-flagged outranks a metric dip.** A case crossing the hallucination threshold is a *behaviour change*, not a rounding error — so it decides the verdict even when the numbers look flat.
- **Cases match on question text, not position or id.** Ids aren't stable across runs, and a reordered suite isn't a changed one. Questions that appear or vanish are reported **separately** rather than silently scored — a vanished case is a change to the *suite*, not evidence about the system.
- **A run knows why it exists.** `source` is `ci` or `ablation`. A retrieval sweep is real data and a wrong answer to "what did this push break?" — only same-config runs answer that — so `latest-comparison` filters to `ci`. This wasn't hypothetical: a seeded k=3/k=2 pair sat next to a real CI run and the endpoint reported `regressed` with five precision drops, blaming a config change on a commit. Every number was right; the question was wrong.
- **A verdict says which two runs produced it.** Both sides of an interesting comparison are usually the *same suite*, so the name can't identify them — `baseline` and `candidate` carry the run id, label and git sha. A verdict you can't trace back to two commits isn't evidence, it's a rumour.

## Run it

```bash
git clone https://github.com/egnaro9/eval-history && cd eval-history
pip install -e ".[dev]"
pytest -q                    # 42 tests, no database required
uvicorn evalhistory.app:app --reload
```

```bash
# WRITE_KEYS has no default -- unset means no key is valid, so a deploy that
# forgets it locks the door instead of accepting one printed in this README.
export WRITE_KEYS=dev-key
uvicorn evalhistory.app:app --reload

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

## Migrations, and the test that makes them mean anything

Schema changes go through **Alembic**; production's schema is whatever the migrations built, and [`migrations/env.py`](migrations/env.py) reads `DATABASE_URL` through the app's own normaliser rather than `alembic.ini`, so a migration can't be aimed at a database the app isn't using.

The part worth stealing is [`tests/test_migrations.py`](tests/test_migrations.py): it builds one database **by running every migration**, another **from the models**, and diffs the resulting schemas — columns, types, nullability, indexes, primary keys, and the FK's `ON DELETE`. Add a column without a migration and CI goes red. Without that check, a model change with no migration is green locally and a 500 in production on a column Postgres has never heard of.

It also earns something subtler. The deployed database predates Alembic — it was built by `create_all()` and has real rows in it — so `ensure_schema` **stamps** it rather than migrating it, which is a claim that the schema it already has is the one the migration would have produced. The drift test is what makes that claim true instead of hopeful.

> **The scary error wasn't the bug.** The first deploy crash-looped with `connection to server at "2600:1f10:…" failed: Network is unreachable` — Neon publishes AAAA records, Render's free tier has no IPv6 egress. So I wrote a `do_connect` hook to resolve the A record and pin `hostaddr` to IPv4. It worked, and it changed nothing: the service still crash-looped. Further down the *same* error, past the IPv6 noise, was an IPv4 address reporting `password authentication failed` — psycopg had been trying every resolved address and falling through to IPv4 on its own the whole time. The real fault was a connection string copied out of a dashboard while the password was still masked: structurally perfect, `**********` where the secret goes. **The IPv4 hook has since been deleted** — it fixed nothing, and code kept to justify the hour that produced it is how a codebase rots. The lesson worth keeping is that I spent that hour on the alarming line instead of the accurate one.

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
  migrate.py   schema at startup: Alembic on Postgres, create_all on SQLite
tests/         33 tests — comparison logic, API, auth, validation, cascade,
               migration/model drift (the drift test needs Postgres; it skips
               on SQLite rather than pretending to check)
```

---

Built by [Erik Hill](https://egnaro9.github.io) · MIT · the other half of [rag-eval-lab](https://github.com/egnaro9/rag-eval-lab) and [eval-dashboard](https://github.com/egnaro9/eval-dashboard).
