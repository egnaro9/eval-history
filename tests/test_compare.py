"""The regression logic — pure, so it's tested directly and hard."""
from evalhistory.compare import DEFAULT_TOLERANCE, compare_runs


def run(name="s", cases=None, metrics=None):
    cases = cases or []
    return {"run": name, "cases": cases, "metrics": metrics or {}}


def case(q, faith=1.0, flagged=False, **rest):
    scores = {"faithfulness": faith, "precision@k": 1.0, "recall@k": 1.0, "citation": 1.0}
    scores.update(rest)
    return {"q": q, "flagged": flagged, "scores": scores}


def test_identical_runs_are_unchanged():
    a = run(cases=[case("q1"), case("q2")])
    c = compare_runs(a, a)
    assert c.verdict == "unchanged"
    assert not c.is_regression
    assert c.regressions == [] and c.improvements == []


def test_detects_a_regression():
    c = compare_runs(run(cases=[case("q1", faith=1.0)]), run(cases=[case("q1", faith=0.4)]))
    assert c.is_regression and c.verdict == "regressed"
    assert len(c.regressions) == 1
    d = c.regressions[0]
    assert d.q == "q1" and d.metric == "faithfulness"
    assert d.before == 1.0 and d.after == 0.4 and d.delta == -0.6


def test_detects_an_improvement():
    c = compare_runs(run(cases=[case("q1", faith=0.4)]), run(cases=[case("q1", faith=0.9)]))
    assert c.verdict == "improved" and not c.is_regression
    assert c.improvements[0].delta == 0.5


def test_noise_below_tolerance_is_not_a_change():
    # Float scores wobble; without a band every run 'regresses' and the signal drowns.
    c = compare_runs(run(cases=[case("q1", faith=0.9000)]), run(cases=[case("q1", faith=0.9004)]))
    assert c.verdict == "unchanged"
    assert c.regressions == []


def test_a_change_just_over_tolerance_is_reported():
    c = compare_runs(run(cases=[case("q1", faith=0.90)]), run(cases=[case("q1", faith=0.88)]))
    assert c.is_regression


def test_newly_flagged_is_a_regression_even_if_metrics_look_flat():
    # Crossing the hallucination threshold is a behaviour change, not a rounding
    # error — it must outrank a quiet metric.
    c = compare_runs(
        run(cases=[case("q1", faith=0.61, flagged=False)]),
        run(cases=[case("q1", faith=0.61, flagged=True)]),
    )
    assert c.newly_flagged == ["q1"]
    assert c.is_regression and c.verdict == "regressed"


def test_newly_clean_is_an_improvement():
    c = compare_runs(
        run(cases=[case("q1", faith=0.6, flagged=True)]),
        run(cases=[case("q1", faith=0.6, flagged=False)]),
    )
    assert c.newly_clean == ["q1"] and c.verdict == "improved"


def test_added_and_removed_cases_are_reported_not_scored():
    # A vanished case is a change to the SUITE, not evidence about the system.
    c = compare_runs(run(cases=[case("old")]), run(cases=[case("new")]))
    assert c.added == ["new"] and c.removed == ["old"]
    assert c.regressions == [] and c.improvements == []


def test_cases_match_on_question_not_position():
    c = compare_runs(
        run(cases=[case("a", faith=1.0), case("b", faith=1.0)]),
        run(cases=[case("b", faith=1.0), case("a", faith=0.2)]),   # reordered
    )
    assert [d.q for d in c.regressions] == ["a"]


def test_a_regression_outranks_an_improvement():
    c = compare_runs(
        run(cases=[case("a", faith=1.0), case("b", faith=0.2)]),
        run(cases=[case("a", faith=0.2), case("b", faith=1.0)]),
    )
    assert c.verdict == "regressed"     # one got better, one got worse -> worse wins


def test_tracks_every_metric_not_just_faithfulness():
    c = compare_runs(
        run(cases=[case("q1", **{"precision@k": 1.0})]),
        run(cases=[case("q1", **{"precision@k": 0.3})]),
    )
    assert [d.metric for d in c.regressions] == ["precision@k"]


def test_missing_metric_on_one_side_is_skipped_not_crashed():
    a = {"run": "s", "cases": [{"q": "q1", "flagged": False, "scores": {"faithfulness": 1.0}}], "metrics": {}}
    b = {"run": "s", "cases": [{"q": "q1", "flagged": False, "scores": {}}], "metrics": {}}
    c = compare_runs(a, b)
    assert c.verdict == "unchanged"


def test_suite_level_metric_deltas():
    c = compare_runs(
        run(metrics={"faithfulness": 0.9, "n_cases": 6.0}),
        run(metrics={"faithfulness": 0.5, "n_cases": 6.0}),
    )
    assert c.metric_deltas["faithfulness"] == -0.4
    assert "n_cases" not in c.metric_deltas     # unchanged -> not noise


def test_empty_runs_do_not_crash():
    assert compare_runs(run(), run()).verdict == "unchanged"
