import pytest
from fastapi.testclient import TestClient

from evalhistory.app import create_app
from evalhistory.db import ENGINE, init_db
from evalhistory.models import Base


@pytest.fixture(autouse=True)
def clean_db():
    """A fresh schema per test.

    Without this the tests share one database and quietly depend on rows left
    behind by whoever ran first — they passed only because the ordering
    happened to suit them. That's fine until the backend changes, the order
    changes, or someone runs a single test. A test that needs its neighbours is
    not a test.

    Same reset on SQLite and Postgres, so a green local run means something
    about the deployed one.
    """
    Base.metadata.drop_all(ENGINE)
    init_db()
    yield
    Base.metadata.drop_all(ENGINE)


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def auth():
    return {"Authorization": "Bearer dev-key"}


@pytest.fixture
def make_run():
    """Factory fixture for an eval_run.json payload.

    A fixture rather than an importable helper: `from tests.conftest import ...`
    only resolves when the rootdir happens to be on sys.path, which is true for
    `python -m pytest` (it adds cwd) and false for bare `pytest`. CI runs the
    latter.
    """
    return _make_run


def _make_run(name="rag-eval-lab", faithfulness=1.0, flagged=False, extra_cases=None,
              label=None, git_sha=None):
    cases = [
        {"q": "Which planet is the hottest?", "answer": "Venus is the hottest.",
         "retrieved": ["venus#0"], "citations": ["venus#0"],
         "scores": {"faithfulness": faithfulness, "precision@k": 1.0, "recall@k": 1.0, "citation": 1.0},
         "flagged": flagged, "note": ""},
        {"q": "Who wrote Hamlet?", "answer": "Shakespeare.",
         "retrieved": ["hamlet#0"], "citations": ["hamlet#0"],
         "scores": {"faithfulness": 1.0, "precision@k": 1.0, "recall@k": 1.0, "citation": 1.0},
         "flagged": False, "note": ""},
    ]
    if extra_cases:
        cases.extend(extra_cases)
    n = len(cases)
    return {
        "run": name,
        "label": label,
        "git_sha": git_sha,
        "metrics": {
            "faithfulness": sum(c["scores"]["faithfulness"] for c in cases) / n,
            "precision@k": 1.0, "recall@k": 1.0, "citation_rate": 1.0,
            "flagged_cases": float(sum(1 for c in cases if c["flagged"])), "n_cases": float(n),
        },
        "cases": cases,
    }
