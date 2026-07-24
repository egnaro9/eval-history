"""Crash-test runs (source="crash_test"): eval-history accepts them, persists a
vulnerability score alongside correctness, hands it back in every shape — and
keeps them off the CI board, so a crash-test run is never mistaken for a
regression someone shipped."""


def _crash_run(make_run, *, name="crashkit", vuln=0.375, flagged=True):
    body = make_run(name=name, flagged=flagged, source="crash_test")
    body["metrics"]["vulnerability_score"] = vuln
    return body


def test_crash_test_source_is_accepted(client, auth, make_run):
    r = client.post("/runs", json=_crash_run(make_run), headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "crash_test"


def test_vulnerability_score_is_persisted_and_returned_everywhere(client, auth, make_run):
    created = client.post("/runs", json=_crash_run(make_run, vuln=0.42), headers=auth).json()
    assert created["vulnerability_score"] == 0.42                       # POST summary
    assert client.get("/runs?name=crashkit").json()[0]["vulnerability_score"] == 0.42  # list
    assert client.get(f"/runs/{created['id']}").json()["vulnerability_score"] == 0.42  # detail
    ev = client.get(f"/runs/{created['id']}/eval_run").json()
    assert ev["metrics"]["vulnerability_score"] == 0.42                 # eval_run shape


def test_correctness_runs_still_ingest_and_carry_no_vulnerability(client, auth, make_run):
    created = client.post("/runs", json=make_run(), headers=auth).json()
    assert created["vulnerability_score"] is None
    ev = client.get(f"/runs/{created['id']}/eval_run").json()
    # unchanged for rag / model-drift: the key is simply absent, not None
    assert "vulnerability_score" not in ev["metrics"]


def test_crash_test_runs_stay_off_the_ci_latest_comparison(client, auth, make_run):
    client.post("/runs", json=_crash_run(make_run, name="crashkit", vuln=0.2), headers=auth)
    client.post("/runs", json=_crash_run(make_run, name="crashkit", vuln=0.5), headers=auth)
    # Two crash-test runs exist, but latest-comparison only looks at source='ci'.
    assert client.get("/suites/crashkit/latest-comparison").status_code == 404


def test_two_crash_test_runs_compare_on_vulnerability(client, auth, make_run):
    a = client.post("/runs", json=_crash_run(make_run, vuln=0.2), headers=auth).json()
    b = client.post("/runs", json=_crash_run(make_run, vuln=0.5), headers=auth).json()
    cmp = client.get(f"/runs/{a['id']}/compare/{b['id']}").json()
    assert cmp["metric_deltas"].get("vulnerability_score") == 0.3


def test_rejects_an_out_of_range_vulnerability_score(client, auth, make_run):
    body = _crash_run(make_run, vuln=1.5)                              # not in 0..1
    assert client.post("/runs", json=body, headers=auth).status_code == 422
