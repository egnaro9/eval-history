"""Regression detection between two eval runs.

This is why eval history exists at all. A single run tells you the score today;
it can't tell you that a change made things *worse*. Storing runs only matters
if you can answer one question well:

    which cases got worse, and by how much?

Deliberately pure — no database, no framework, no I/O. The persistence layer
hands it two runs and it returns a verdict, which means the thing worth being
correct is the thing that's trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# A metric has to move by more than this to count as a change. Float scores
# wobble in the last decimal; without a band, every run "regresses" and the
# signal drowns.
DEFAULT_TOLERANCE = 0.005

# Higher is better for all of these.
TRACKED = ("faithfulness", "precision@k", "recall@k", "citation")


@dataclass
class CaseDelta:
    q: str
    metric: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return round(self.after - self.before, 4)

    @property
    def regressed(self) -> bool:
        return self.after < self.before


@dataclass
class Comparison:
    baseline: str
    candidate: str
    regressions: List[CaseDelta] = field(default_factory=list)
    improvements: List[CaseDelta] = field(default_factory=list)
    newly_flagged: List[str] = field(default_factory=list)
    newly_clean: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    metric_deltas: Dict[str, float] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """A single word, because CI needs to branch on something.

        newly_flagged outranks a metric dip: a case crossing the hallucination
        threshold is a behaviour change, not a rounding error.
        """
        if self.newly_flagged:
            return "regressed"
        if self.regressions:
            return "regressed"
        if self.improvements or self.newly_clean:
            return "improved"
        return "unchanged"

    @property
    def is_regression(self) -> bool:
        return self.verdict == "regressed"


def _by_question(cases: Sequence[dict]) -> Dict[str, dict]:
    return {c["q"]: c for c in cases}


def compare_runs(
    baseline: dict,
    candidate: dict,
    tolerance: float = DEFAULT_TOLERANCE,
    metrics: Sequence[str] = TRACKED,
) -> Comparison:
    """Compare two runs in rag-eval-lab's eval_run schema.

    Cases are matched on the question text — an eval set is a set of questions,
    and ids aren't stable across runs. Questions that appear or disappear are
    reported separately rather than silently scored, because a vanished case is
    a change to the *suite*, not to the system under test.
    """
    b_cases = _by_question(baseline.get("cases", []))
    c_cases = _by_question(candidate.get("cases", []))

    cmp = Comparison(
        baseline=baseline.get("run", "baseline"),
        candidate=candidate.get("run", "candidate"),
        added=sorted(set(c_cases) - set(b_cases)),
        removed=sorted(set(b_cases) - set(c_cases)),
    )

    for q in sorted(set(b_cases) & set(c_cases)):
        b, c = b_cases[q], c_cases[q]
        for m in metrics:
            before = b.get("scores", {}).get(m)
            after = c.get("scores", {}).get(m)
            if before is None or after is None:
                continue
            if abs(after - before) <= tolerance:
                continue
            d = CaseDelta(q=q, metric=m, before=round(before, 4), after=round(after, 4))
            (cmp.regressions if d.regressed else cmp.improvements).append(d)

        # Crossing the flag threshold is the headline event either way.
        if not b.get("flagged") and c.get("flagged"):
            cmp.newly_flagged.append(q)
        elif b.get("flagged") and not c.get("flagged"):
            cmp.newly_clean.append(q)

    bm, cm = baseline.get("metrics", {}), candidate.get("metrics", {})
    for k in set(bm) & set(cm):
        delta = round(cm[k] - bm[k], 4)
        if abs(delta) > tolerance:
            cmp.metric_deltas[k] = delta

    return cmp
