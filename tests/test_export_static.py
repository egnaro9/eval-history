"""The static export must answer every read route byte-identically.

The point of tools/export_static.py is that the archive outlives the host: the
service was suspended over an unpaid invoice and every /runs link went 503. A
static tree only replaces the API if it returns the SAME bytes, so this test
diffs the export against the live app rather than asserting the files exist.

That distinction is not theoretical. The first version of the exporter wrote
'<evalhistory.models.Run object at 0x...>' into all twelve comparison files,
because _comparison_out hands back ORM objects that FastAPI serialized through
ComparisonOut and the exporter did not. It reported 23 files written and a clean
manifest hash. Only diffing against the app caught it.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evalhistory.app import create_app
from evalhistory.db import SessionLocal
from evalhistory.models import Case, Run

REPO = Path(__file__).resolve().parents[1]


def _run_export(out: Path, monkeypatch) -> int:
    """Call the exporter IN-PROCESS, not as a subprocess.

    conftest binds the engine to an in-memory SQLite database, which no separate
    process can open: a subprocess gets a fresh empty file and the exporter
    correctly refuses to write an empty archive. In-process it shares the very
    rows the fixture seeded, which is what the comparison needs.
    """
    spec = importlib.util.spec_from_file_location(
        "export_static", REPO / "tools" / "export_static.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setattr("sys.argv", ["export_static.py", "--out", str(out)])
    return mod.main()


def _mk(session, rid, name, source, when, cases):
    r = Run(id=rid, name=name, source=source, created_at=when, git_sha="a" * 40,
            label=f"{name} {source}", faithfulness=0.75, precision_at_k=0.65,
            recall_at_k=0.60, citation_rate=0.50, flagged_cases=1, n_cases=len(cases))
    for i, (q, flagged) in enumerate(cases):
        r.cases.append(Case(run_id=rid, q=q, answer="a", note=None, retrieved=[],
                            citations=[], faithfulness=0.9, precision_at_k=0.8,
                            recall_at_k=0.7, citation=1.0, flagged=flagged, ordinal=i))
    session.add(r)
    return r


@pytest.fixture
def seeded():
    s = SessionLocal()
    base = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
    cases = [("what is x?", False), ("what is y?", True)]
    _mk(s, "aaaa1111", "suite-a", "ci", base, cases)
    _mk(s, "bbbb2222", "suite-a", "ci", base + dt.timedelta(days=1), cases)
    # An ablation, which latest-comparison must ignore, and a suite with only one
    # CI run, which must produce no file because the live route 404s.
    _mk(s, "cccc3333", "suite-a", "ablation", base + dt.timedelta(days=2), cases)
    _mk(s, "dddd4444", "suite-b", "ci", base + dt.timedelta(days=3), cases)
    s.commit()
    yield s
    s.close()


def test_export_matches_every_live_read_route(seeded, tmp_path, monkeypatch):
    out = tmp_path / "export"
    assert _run_export(out, monkeypatch) == 0

    client = TestClient(create_app())
    ids = [r["id"] for r in json.loads((out / "runs.json").read_text())]
    assert len(ids) == 4

    def same(url: str, rel: str) -> None:
        live = client.get(url)
        assert live.status_code == 200, url
        assert json.loads((out / rel).read_text()) == live.json(), url

    same("/runs", "runs.json")
    for i in ids:
        same(f"/runs/{i}", f"runs/{i}.json")
        same(f"/runs/{i}/eval_run", f"runs/{i}/eval_run.json")
    for a in ids:
        for b in ids:
            if a != b:
                same(f"/runs/{a}/compare/{b}", f"runs/{a}/compare/{b}.json")
    same("/suites/suite-a/latest-comparison", "suites/suite-a/latest-comparison.json")


def test_suite_without_two_ci_runs_gets_no_file(seeded, tmp_path, monkeypatch):
    """suite-b has one CI run. The live route 404s, so the export must be silent
    rather than emit a file that answers a different question."""
    out = tmp_path / "export"
    assert _run_export(out, monkeypatch) == 0
    assert TestClient(create_app()).get("/suites/suite-b/latest-comparison").status_code == 404
    assert not (out / "suites" / "suite-b" / "latest-comparison.json").exists()


def test_manifest_records_no_credentials(seeded, tmp_path, monkeypatch):
    """The manifest names the host and nothing else: no user, password, or dbname."""
    out = tmp_path / "export"
    assert _run_export(out, monkeypatch) == 0
    text = (out / "manifest.json").read_text()
    manifest = json.loads(text)
    assert set(manifest) == {"exported_at", "source_host", "run_count", "file_count", "files"}
    for secret in ("password", "@", "://"):
        assert secret not in manifest["source_host"]
