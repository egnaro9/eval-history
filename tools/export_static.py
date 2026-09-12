"""Export the whole history to static JSON, so it outlives any host.

Render suspended the service over an unpaid invoice and every /runs link on
erikhill.dev went 503. The runs themselves are recorded evidence, not live
computation: eight of the nine routes are reads, and a comparison is a pure
function of two run dicts (see compare.compare_runs). So the archive can be
served by anything that serves files, including GitHub Pages, which has no
billing to lapse.

This writes a tree that mirrors the API paths exactly, so a static host answers
the same URLs the service did:

    runs.json                         GET /runs
    runs/<id>.json                    GET /runs/{run_id}
    runs/<id>/eval_run.json           GET /runs/{run_id}/eval_run
    runs/<a>/compare/<b>.json         GET /runs/{a}/compare/{b}
    suites/<name>/latest-comparison.json
                                      GET /suites/{name}/latest-comparison
    manifest.json                     sha256 of every file above

Serialization goes through the same Pydantic response models the app used, and
comparisons through the same compare_runs, so the static bytes are produced by
the code that produced the live ones rather than by a reimplementation.

Usage, with the Neon connection string in the environment and never on the
command line, where it would land in shell history:

    export DATABASE_URL='postgresql://...'      # from the Render dashboard
    python tools/export_static.py --out export

The URL is read from the environment, never logged, and never written into any
output file. manifest.json records the host only, not credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit


def _dump(model_cls, obj):
    """Serialize through the app's response model, pydantic v1 or v2."""
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(obj, from_attributes=True).model_dump(mode="json")
    if isinstance(obj, dict):
        return json.loads(model_cls(**obj).json())
    return json.loads(model_cls.from_orm(obj).json())


def _write(root: pathlib.Path, rel: str, payload) -> tuple[str, str]:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n"
    p.write_bytes(text.encode())
    return rel, hashlib.sha256(text.encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="export", help="output directory")
    ap.add_argument("--max-pairs", type=int, default=2000,
                    help="refuse to emit more comparison files than this")
    args = ap.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set. Export it first; do not pass it as an argument.",
              file=sys.stderr)
        return 2

    from sqlalchemy import select
    from evalhistory.app import _comparison_out, _run_to_dict
    from evalhistory.compare import compare_runs
    from evalhistory.db import session_scope
    from evalhistory.models import Run
    from evalhistory import schemas

    RunSummary = schemas.RunSummary
    RunDetail = schemas.RunDetail
    # _comparison_out hands back the Run ORM objects for baseline and candidate;
    # live, FastAPI serialized those through ComparisonOut. Dumping the raw dict
    # writes "<evalhistory.models.Run object at 0x...>" into the file instead.
    # Caught by diffing this export against the live app before shipping it.
    ComparisonOut = schemas.ComparisonOut

    root = pathlib.Path(args.out)
    files: dict[str, str] = {}

    with session_scope() as db:
        runs = list(db.scalars(select(Run).order_by(Run.created_at.desc())))
        if not runs:
            print("no runs found; refusing to write an empty export", file=sys.stderr)
            return 1
        print(f"runs: {len(runs)}")

        # GET /runs, unpaginated: the static index is the whole ordered list.
        rel, sha = _write(root, "runs.json", [_dump(RunSummary, r) for r in runs])
        files[rel] = sha

        for r in runs:
            rel, sha = _write(root, f"runs/{r.id}.json", _dump(RunDetail, r))
            files[rel] = sha
            rel, sha = _write(root, f"runs/{r.id}/eval_run.json", _run_to_dict(r))
            files[rel] = sha

        # Every ordered pair, because "a" is the baseline and the comparison is
        # not symmetric. Guarded: N runs give N*(N-1) files.
        n_pairs = len(runs) * (len(runs) - 1)
        if n_pairs > args.max_pairs:
            print(f"{n_pairs} ordered pairs exceeds --max-pairs {args.max_pairs}; "
                  f"emitting none. Raise the limit deliberately if you want them.",
                  file=sys.stderr)
        else:
            dicts = {r.id: _run_to_dict(r) for r in runs}
            by_id = {r.id: r for r in runs}
            for a in runs:
                for b in runs:
                    if a.id == b.id:
                        continue
                    out = _dump(ComparisonOut,
                                _comparison_out(compare_runs(dicts[a.id], dicts[b.id]),
                                                by_id[a.id], by_id[b.id]))
                    rel, sha = _write(root, f"runs/{a.id}/compare/{b.id}.json", out)
                    files[rel] = sha
            print(f"comparisons: {n_pairs}")

        # Same filter the live route used: CI runs only, ablations excluded on
        # purpose, newest two. A suite with fewer than two CI runs 404'd live and
        # gets no file here, rather than a file that answers a different question.
        suites = sorted({r.name for r in runs})
        emitted = 0
        for name in suites:
            ci = list(db.scalars(
                select(Run)
                .where(Run.name == name, Run.source == "ci")
                .order_by(Run.created_at.desc())
                .limit(2)
            ))
            if len(ci) < 2:
                print(f"  suite {name!r}: {len(ci)} ci run(s), skipped (live route 404'd too)")
                continue
            newest, previous = ci[0], ci[1]
            out = _dump(ComparisonOut, _comparison_out(
                compare_runs(_run_to_dict(previous), _run_to_dict(newest)), previous, newest))
            rel, sha = _write(root, f"suites/{name}/latest-comparison.json", out)
            files[rel] = sha
            emitted += 1
        print(f"suites: {len(suites)} seen, {emitted} with a latest-comparison")

    host = urlsplit(os.environ["DATABASE_URL"]).hostname or "unknown"
    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_host": host,          # host only; no user, password, or database name
        "run_count": len(runs),
        "file_count": len(files),
        "files": dict(sorted(files.items())),
    }
    rel, sha = _write(root, "manifest.json", manifest)
    print(f"\nwrote {len(files) + 1} files to {root}/")
    print(f"manifest sha256: {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
