"""The API.

    POST /runs                  store an eval_run.json
    GET  /runs                  list, newest first
    GET  /runs/{id}             one run with its cases
    GET  /runs/{a}/compare/{b}  what changed — the reason this exists
    GET  /runs/latest/compare   the two most recent runs of a suite
    DELETE /runs/{id}           (write key required)

Writes need a key; reads are open. That asymmetry is deliberate: the point of
publishing this is that anyone can look, and nobody can scribble on it.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__
from .compare import CaseDelta, Comparison, compare_runs
from .db import get_session
from .models import Case, Run
from .obs import RequestObservability, configure_logging, logger, metrics_response
from .schemas import ComparisonOut, RunDetail, RunIn, RunSummary


def _write_keys() -> set[str]:
    """Configured write keys. No default, on purpose.

    This used to default to "dev-key". A deploy that forgot to set WRITE_KEYS
    would then accept a key printed in this repo's README — the whole internet
    holds the credential. Defaulting to empty fails closed for free: the
    comprehension drops blanks, the set is empty, and every token mismatches.
    Unset config should lock the door, not leave a documented key under the mat.
    """
    return {k.strip() for k in os.environ.get("WRITE_KEYS", "").split(",") if k.strip()}


# auto_error=False so a missing header reaches our own check and returns the
# same 401 as a wrong key — an anonymous caller learns "you need a key", not
# "you sent the wrong kind of header". HTTPBearer (rather than a raw Header)
# also puts padlocks and an Authorize button on /docs, which is public.
_bearer = HTTPBearer(auto_error=False, description="A write key. Reads need nothing.")


def require_write_key(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    token = (creds.credentials if creds else "").strip()
    keys = _write_keys()
    if not keys or token not in keys:
        raise HTTPException(status_code=401, detail="a write key is required")
    return token


def _run_to_dict(run: Run) -> dict:
    """Back to the eval_run shape the comparer speaks."""
    return {
        "run": run.name,
        "metrics": {
            "faithfulness": run.faithfulness,
            "precision@k": run.precision_at_k,
            "recall@k": run.recall_at_k,
            "citation_rate": run.citation_rate,
            "flagged_cases": float(run.flagged_cases),
            "n_cases": float(run.n_cases),
        },
        "cases": [
            {
                "q": c.q,
                "flagged": c.flagged,
                "scores": {
                    "faithfulness": c.faithfulness,
                    "precision@k": c.precision_at_k,
                    "recall@k": c.recall_at_k,
                    "citation": c.citation,
                },
            }
            for c in run.cases
        ],
    }


def _delta_out(d: CaseDelta) -> dict:
    return {"q": d.q, "metric": d.metric, "before": d.before, "after": d.after, "delta": d.delta}


def _comparison_out(c: Comparison, baseline: Run, candidate: Run) -> dict:
    """`compare_runs` works on eval_run dicts, which only carry a suite name.

    The runs themselves are what the caller needs to identify a verdict, so the
    identity is attached here rather than smuggled into the pure comparer.
    """
    return {
        "baseline": baseline, "candidate": candidate,
        "verdict": c.verdict, "is_regression": c.is_regression,
        "regressions": [_delta_out(d) for d in c.regressions],
        "improvements": [_delta_out(d) for d in c.improvements],
        "newly_flagged": c.newly_flagged, "newly_clean": c.newly_clean,
        "added": c.added, "removed": c.removed, "metric_deltas": c.metric_deltas,
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Migrations run here rather than in a release phase because the free tier
    # has no release phase and runs a single instance. With more than one
    # instance this races and belongs in a pre-deploy step instead.
    from .db import ENGINE
    from .migrate import ensure_schema

    logger.info("schema ensured", extra={"schema": ensure_schema(ENGINE)})
    yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="eval-history",
        version=__version__,
        summary="Store eval runs and find out what got worse.",
        description=(
            "Writes need a key; reads are open. That asymmetry is deliberate: the point "
            "of publishing this is that anyone can look, and nobody can scribble on it.\n\n"
            "Ingests [rag-eval-lab](https://github.com/egnaro9/rag-eval-lab)'s "
            "`eval_run.json` verbatim, and hands it back in the same shape from "
            "`/runs/{id}/eval_run` — so anything that already speaks that file needs no adapter."
        ),
        lifespan=lifespan,
    )
    # The dashboard is a static site on another origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in os.environ.get(
            "CORS_ORIGINS", "https://egnaro9.github.io,http://localhost:3000"
        ).split(",")],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    # Added last so it wraps CORS and everything else — it therefore times the
    # whole request and sees the final status, request id, and route template.
    app.add_middleware(RequestObservability)

    @app.get("/metrics", tags=["ops"], summary="Prometheus metrics")
    def metrics() -> Response:
        """Request counts and latency, labeled by method, route template, and
        status. Scraped by Prometheus; excluded from its own instrumentation so
        the scrape loop doesn't dominate the numbers it reports."""
        return metrics_response()

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        """Liveness: is this process up? Deliberately touches nothing.

        `render.yaml` points healthCheckPath here, and Render restarts the
        instance when it fails. Checking the database here would turn a Neon
        blip into a restart, which drops the connections that might have
        recovered — a crashloop caused by the monitoring. Liveness answers
        "should I be restarted?", and a database outage is never a yes.
        Use /readyz to ask whether it can actually serve.
        """
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["ops"])
    def readyz(db: Session = Depends(get_session)) -> dict:
        """Readiness: can it serve a request end to end, database included?

        Safe to fail — nothing restarts on this. 503 so a load balancer or an
        uptime check can route away while the process stays alive.
        """
        try:
            db.execute(select(1))
        except Exception as e:  # noqa: BLE001 - report the class, not a stack trace
            raise HTTPException(status_code=503, detail=f"database unreachable: {type(e).__name__}")
        return {"status": "ready", "version": __version__}

    @app.post("/runs", response_model=RunSummary, status_code=201)
    def create_run(
        payload: RunIn,
        db: Session = Depends(get_session),
        _key: str = Depends(require_write_key),
    ) -> Run:
        m = payload.metrics
        run = Run(
            name=payload.run,
            git_sha=payload.git_sha,
            label=payload.label,
            source=payload.source,
            faithfulness=m.faithfulness,
            precision_at_k=m.precision_at_k,
            recall_at_k=m.recall_at_k,
            citation_rate=m.citation_rate,
            flagged_cases=int(m.flagged_cases),
            n_cases=int(m.n_cases),
        )
        for i, c in enumerate(payload.cases):
            run.cases.append(
                Case(
                    ordinal=i,
                    q=c.q, answer=c.answer, note=c.note,
                    retrieved=c.retrieved, citations=c.citations,
                    faithfulness=c.scores.faithfulness,
                    precision_at_k=c.scores.precision_at_k,
                    recall_at_k=c.scores.recall_at_k,
                    citation=c.scores.citation,
                    flagged=c.flagged,
                )
            )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    @app.get("/runs", response_model=List[RunSummary])
    def list_runs(
        name: Optional[str] = Query(None, description="only this suite"),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_session),
    ) -> List[Run]:
        stmt = select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset)
        if name:
            stmt = stmt.where(Run.name == name)
        return list(db.scalars(stmt))

    @app.get("/runs/{run_id}", response_model=RunDetail)
    def get_run(run_id: str, db: Session = Depends(get_session)) -> Run:
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return run

    @app.get("/runs/{run_id}/eval_run")
    def get_run_as_eval_run(run_id: str, db: Session = Depends(get_session)) -> dict:
        """The run in the exact shape it arrived in.

        `POST /runs` takes rag-eval-lab's eval_run.json verbatim; this hands the
        same shape back. Consumers that already understand eval_run.json — the
        dashboard, a notebook, whatever produced it — need no adapter, and the
        storage layout stays free to be a normalised schema rather than a
        wire format leaked into the database.
        """
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return _run_to_dict(run)

    @app.get("/runs/{a}/compare/{b}", response_model=ComparisonOut)
    def compare(a: str, b: str, db: Session = Depends(get_session)) -> dict:
        """What changed between two runs. `a` is the baseline."""
        ra, rb = db.get(Run, a), db.get(Run, b)
        if ra is None or rb is None:
            raise HTTPException(status_code=404, detail="no such run")
        return _comparison_out(compare_runs(_run_to_dict(ra), _run_to_dict(rb)), ra, rb)

    @app.get("/suites/{name}/latest-comparison", response_model=ComparisonOut)
    def latest_comparison(name: str, db: Session = Depends(get_session)) -> dict:
        """The two most recent CI runs of a suite — the CI-friendly shortcut.

        Ablations are excluded on purpose. This endpoint answers "did the last
        push break anything", and only same-config runs can answer it: comparing
        a `k=2` sweep against a commit reports five precision drops and blames
        them on whoever pushed. Real numbers, wrong question.
        """
        runs = list(db.scalars(
            select(Run)
            .where(Run.name == name, Run.source == "ci")
            .order_by(Run.created_at.desc())
            .limit(2)
        ))
        if len(runs) < 2:
            raise HTTPException(status_code=404, detail="need at least two CI runs of this suite")
        newest, previous = runs[0], runs[1]
        return _comparison_out(
            compare_runs(_run_to_dict(previous), _run_to_dict(newest)), previous, newest
        )

    @app.delete("/runs/{run_id}", status_code=204)
    def delete_run(
        run_id: str,
        db: Session = Depends(get_session),
        _key: str = Depends(require_write_key),
    ) -> None:
        run = db.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        db.delete(run)   # cases cascade
        db.commit()

    return app


app = create_app()
