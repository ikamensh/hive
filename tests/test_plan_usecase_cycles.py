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


def test_repeated_cancellation_preserves_the_reason_in_the_result(app):
    """Retrying a cancellation after a lost response must preserve the owner's
    explanation and timestamp in the durable execution receipt."""
    client, _ = app
    project = _api_project(client)
    created = client.post(f"/api/projects/{project}/plan", json={
        "goal": "Ship exports", "items": [{"title": "CSV export"}, {"title": "PDF export"}],
    }).raise_for_status().json()
    item_id = created["items"][1]["id"]
    cancelled = client.post(f"/api/plan-items/{item_id}/cancel", json={
        "reason": "PDF export is deferred until customers request it.",
    }).raise_for_status().json()

    duplicate = client.post(f"/api/plan-items/{item_id}/cancel").raise_for_status().json()

    assert duplicate == cancelled
    result = client.get(f"/api/plans/{created['plan']['id']}/result").raise_for_status().json()
    assert result["items"][1]["status"] == "cancelled"
    assert result["items"][1]["parked_reason"] == cancelled["parked_reason"]


def test_markdown_goal_links_survive_import_and_export(app):
    """A hand-authored Markdown goal may link its reference design before the
    task headings; the source and executable instructions must survive import."""
    from hive._workstreams.plan_document import parse_plan_document

    client, _ = app
    project = _api_project(client)
    source = (
        "# Publish an accessible export guide\n\n"
        "[Design reference](https://example.invalid/export-design)\n\n"
        "## Write the guide\nInclude a copyable Markdown example:\n\n"
        "```markdown\n## This is example content, not another task\n```\n"
        "\n## Link the guide\nLink it from the export command's help.\n"
    )

    response = client.post(f"/api/projects/{project}/plan/submit", json={"source": source})

    assert response.status_code == 200, response.text
    accepted = response.json()
    assert [item["title"] for item in accepted["items"]] == ["Write the guide", "Link the guide"]
    assert "[Design reference]" in accepted["plan"]["goal"]
    plan_id = accepted["plan"]["id"]
    assert client.get(f"/api/plans/{plan_id}/input").raise_for_status().text == source
    exported = parse_plan_document(client.get(f"/api/plans/{plan_id}/document").raise_for_status().text)
    assert "## This is example content, not another task" in exported["items"][0]["notes"]
    assert len(exported["items"]) == 2
