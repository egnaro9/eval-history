"""The API's typed contract.

`RunIn` accepts rag-eval-lab's `eval_run.json` verbatim — that file is the
integration point between the two projects, so the API takes it as-is rather
than inventing a wire format and making callers translate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Scores(BaseModel):
    faithfulness: float = Field(..., ge=0, le=1)
    precision_at_k: float = Field(..., ge=0, le=1, alias="precision@k")
    recall_at_k: float = Field(..., ge=0, le=1, alias="recall@k")
    citation: float = Field(..., ge=0, le=1)

    model_config = {"populate_by_name": True}


class CaseIn(BaseModel):
    q: str
    answer: str = ""
    retrieved: List[str] = []
    citations: List[str] = []
    scores: Scores
    flagged: bool = False
    note: str = ""


class Metrics(BaseModel):
    faithfulness: float
    precision_at_k: float = Field(..., alias="precision@k")
    recall_at_k: float = Field(..., alias="recall@k")
    citation_rate: float
    flagged_cases: float
    n_cases: float

    model_config = {"populate_by_name": True}


class RunIn(BaseModel):
    """An eval_run.json, exactly as rag-eval-lab writes it."""

    run: str = Field(..., min_length=1, max_length=200)
    metrics: Metrics
    cases: List[CaseIn] = Field(..., min_length=1)
    git_sha: Optional[str] = Field(None, max_length=40)
    label: Optional[str] = Field(None, max_length=200)


class RunSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    faithfulness: float
    precision_at_k: float
    recall_at_k: float
    citation_rate: float
    flagged_cases: int
    n_cases: int
    git_sha: Optional[str] = None
    label: Optional[str] = None

    model_config = {"from_attributes": True}


class CaseOut(BaseModel):
    q: str
    answer: str
    retrieved: List[str]
    citations: List[str]
    faithfulness: float
    precision_at_k: float
    recall_at_k: float
    citation: float
    flagged: bool
    note: str

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    cases: List[CaseOut] = []


class DeltaOut(BaseModel):
    q: str
    metric: str
    before: float
    after: float
    delta: float


class RunRef(BaseModel):
    """Which run, exactly.

    A comparison that says only "rag-eval-lab vs rag-eval-lab" is unreadable —
    both sides of an interesting comparison are usually the same suite. The
    identity lives here so a verdict can be traced back to two specific runs
    and the commits behind them.
    """
    id: str
    name: str
    label: Optional[str] = None
    git_sha: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ComparisonOut(BaseModel):
    baseline: RunRef
    candidate: RunRef
    verdict: str
    is_regression: bool
    regressions: List[DeltaOut] = []
    improvements: List[DeltaOut] = []
    newly_flagged: List[str] = []
    newly_clean: List[str] = []
    added: List[str] = []
    removed: List[str] = []
    metric_deltas: Dict[str, float] = {}
