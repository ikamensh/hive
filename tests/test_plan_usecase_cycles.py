"""Operator workflows discovered while exercising plans, revisions, and receipts."""

from test_plans import FakeSpecRepo, _api_project, app as app


def test_editing_queued_instructions_waits_for_renewed_approval(app, monkeypatch):
    """A revised queued item must stop the ordered queue until its new
    instructions are approved, and the next attempt receives that revision."""
    from test_api_e2e import _pump, _register_usable_runner
    from test_cli import RUNNER_HEADERS

    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    monkeypatch.setattr("hive.api.merge_branch", lambda *args, **kwargs: "a" * 40)
    monkeypatch.setattr("hive.api.delete_branch", lambda *args, **kwargs: None)
    project = _api_project(client)
    client.patch(f"/api/projects/{project}", json={
        "included_only": True, "testing_auto": False,
        "build_backend": "codex", "review_backend": "codex",
    }).raise_for_status()
    created = client.post(f"/api/projects/{project}/plan", json={
        "goal": "Publish CSV exports",
        "items": [{"title": "Export records"}, {"title": "Filter exports"}, {"title": "Document exports"}],
    }).raise_for_status().json()
    plan_id = created["plan"]["id"]
    second = created["items"][1]["id"]
    client.post(f"/api/plans/{plan_id}/approve").raise_for_status()
    runner = _register_usable_runner(client, backend="codex")

    edited = client.patch(f"/api/plan-items/{second}", json={
        "constraints": "Export only records matching the selected date range.",
    }).raise_for_status().json()
    assert edited["status"] == "proposed"
    revision = client.get(f"/api/plans/{plan_id}").raise_for_status().json()["plan"]["revision"]

    for report in ("CSV output implemented.\nOUTCOME: FIXED", "CSV output verified.\nREVIEW: ACCEPT"):
        _pump(client, store)
        task = client.post(f"/api/runners/{runner}/poll", headers=RUNNER_HEADERS).raise_for_status().json()["task"]
        client.post(f"/api/tasks/{task['id']}/result", headers=RUNNER_HEADERS,
                    json={"text": report}).raise_for_status()

    waiting = client.get(f"/api/plans/{plan_id}").raise_for_status().json()
    assert [item["status"] for item in waiting["items"]] == ["done", "proposed", "queued"]
    assert all(task["status"] == "done" for task in waiting["tasks"])
    client.post(f"/api/plan-items/{second}/approve").raise_for_status()
    _pump(client, store)
    resumed = client.post(f"/api/runners/{runner}/poll", headers=RUNNER_HEADERS).raise_for_status().json()["task"]
    assert resumed["work_item_id"] == second
    assert resumed["plan_revision"] == revision
    assert "selected date range" in resumed["instructions"]
