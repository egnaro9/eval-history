import pytest
from fastapi.testclient import TestClient

from evalhistory.app import create_app
from evalhistory.db import init_db


@pytest.fixture
def client(monkeypatch):
    # SQLite in memory — the suite runs with no database installed and no network.
    # The models, queries and constraints are the same ones Postgres sees.
    init_db()
    return TestClient(create_app())


@pytest.fixture
def auth():
    return {"Authorization": "Bearer dev-key"}


def make_run(name="rag-eval-lab", faithfulness=1.0, flagged=False, extra_cases=None):
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
        "metrics": {
            "faithfulness": sum(c["scores"]["faithfulness"] for c in cases) / n,
            "precision@k": 1.0, "recall@k": 1.0, "citation_rate": 1.0,
            "flagged_cases": float(sum(1 for c in cases if c["flagged"])), "n_cases": float(n),
        },
        "cases": cases,
    }
