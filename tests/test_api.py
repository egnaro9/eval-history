"""The API, exercised against a real database — real SQL, real FK cascade."""


def post(client, auth, make_run, **kw):
    r = client.post("/runs", json=make_run(**kw), headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_writes_need_a_key(client, make_run):
    assert client.post("/runs", json=make_run()).status_code == 401
    assert client.post("/runs", json=make_run(), headers={"Authorization": "Bearer nope"}).status_code == 401


def test_reads_are_open(client):
    assert client.get("/runs").status_code == 200      # no key


def test_store_and_fetch_a_run(client, auth, make_run):
    created = post(client, auth, make_run)
    assert created["n_cases"] == 2
    got = client.get(f"/runs/{created['id']}").json()
    assert got["name"] == "rag-eval-lab"
    assert len(got["cases"]) == 2
    assert got["cases"][0]["retrieved"] == ["venus#0"]


def test_accepts_rag_eval_labs_json_verbatim(client, auth, make_run):
    # The aliased keys are the whole point: eval_run.json posts unmodified.
    body = make_run()
    assert "precision@k" in body["metrics"] and "precision@k" in body["cases"][0]["scores"]
    assert client.post("/runs", json=body, headers=auth).status_code == 201


def test_404_on_unknown_run(client):
    assert client.get("/runs/nope").status_code == 404


def test_validation_rejects_a_bad_score(client, auth, make_run):
    body = make_run()
    body["cases"][0]["scores"]["faithfulness"] = 5.0     # out of 0..1
    assert client.post("/runs", json=body, headers=auth).status_code == 422


def test_validation_rejects_an_empty_suite(client, auth, make_run):
    body = make_run()
    body["cases"] = []
    assert client.post("/runs", json=body, headers=auth).status_code == 422


def test_list_is_newest_first_and_paginates(client, auth, make_run):
    for i in range(3):
        post(client, auth, make_run, name=f"suite-{i}")
    runs = client.get("/runs?limit=2").json()
    assert len(runs) == 2
    assert client.get("/runs?limit=2&offset=2").json()[0]["name"] == "suite-0"


def test_list_filters_by_suite(client, auth, make_run):
    post(client, auth, make_run, name="alpha")
    post(client, auth, make_run, name="beta")
    assert [r["name"] for r in client.get("/runs?name=alpha").json()] == ["alpha"]


def test_compare_two_stored_runs(client, auth, make_run):
    a = post(client, auth, make_run, faithfulness=1.0)
    b = post(client, auth, make_run, faithfulness=0.3, flagged=True)
    c = client.get(f"/runs/{a['id']}/compare/{b['id']}").json()
    assert c["is_regression"] and c["verdict"] == "regressed"
    assert c["newly_flagged"] == ["Which planet is the hottest?"]
    assert c["regressions"][0]["delta"] == -0.7


def test_compare_404s_on_unknown_run(client, auth, make_run):
    a = post(client, auth, make_run)
    assert client.get(f"/runs/{a['id']}/compare/nope").status_code == 404


def test_latest_comparison_for_a_suite(client, auth, make_run):
    post(client, auth, make_run, name="ci", faithfulness=1.0)
    post(client, auth, make_run, name="ci", faithfulness=0.4)      # newest
    c = client.get("/suites/ci/latest-comparison").json()
    assert c["is_regression"]           # baseline = previous, candidate = newest


def test_latest_comparison_needs_two_runs(client, auth, make_run):
    post(client, auth, make_run, name="lonely")
    assert client.get("/suites/lonely/latest-comparison").status_code == 404


def test_delete_cascades_to_cases(client, auth, make_run):
    from sqlalchemy import func, select
    from evalhistory.db import SessionLocal
    from evalhistory.models import Case

    created = post(client, auth, make_run)
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Case).where(Case.run_id == created["id"])) == 2
    assert client.delete(f"/runs/{created['id']}", headers=auth).status_code == 204
    assert client.get(f"/runs/{created['id']}").status_code == 404
    with SessionLocal() as s:
        # The FK cascade has to actually fire, or deleted runs leak their cases forever.
        assert s.scalar(select(func.count()).select_from(Case).where(Case.run_id == created["id"])) == 0


def test_delete_needs_a_key(client, auth, make_run):
    created = post(client, auth, make_run)
    assert client.delete(f"/runs/{created['id']}").status_code == 401
