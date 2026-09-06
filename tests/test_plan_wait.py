"""Bounded monitoring stays attached to the submitted plan across later work."""

import json
from types import SimpleNamespace

import pytest

from test_api_e2e import RUNNER_HEADERS, _pump, _register_usable_runner
from test_plans import FakeSpecRepo, app as app

from hive.cli import main


def _approved_plan(client, monkeypatch):
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    project = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    client.patch(f"/api/projects/{project['id']}", json={
        "spec_repo": "https://github.com/o/r.git", "build_backend": "codex", "review_backend": "codex",
    }).raise_for_status()
    draft = client.post(f"/api/projects/{project['id']}/plan", json={
        "goal": "First iteration", "items": [{"title": "First task"}],
    }).raise_for_status().json()["plan"]
    client.post(f"/api/plans/{draft['id']}/approve").raise_for_status()
    return project, draft


def test_wait_observes_submitted_plan_after_replacement(app, monkeypatch, capsys):
    """A historical plan's outcome wins over the project's newer active plan;
    waiting is read-only and emits one machine-readable observation."""
    client, _ = app
    project = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    first = client.post(f"/api/projects/{project['id']}/plan", json={
        "goal": "First iteration", "items": [{"title": "First task"}],
    }).raise_for_status().json()["plan"]
    client.post(f"/api/plans/{first['id']}/abandon").raise_for_status()
    second = client.post(f"/api/projects/{project['id']}/plan", json={
        "goal": "Second iteration", "items": [{"title": "Second task"}],
    }).raise_for_status().json()["plan"]
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)

    main(["--local", "plan-wait", first["id"], "--timeout", "1", "--json"])

    observation = json.loads(capsys.readouterr().out)
    assert observation["reason"] == "abandoned"
    assert observation["plan_id"] == first["id"]
    assert observation["snapshot"]["plan"]["id"] == first["id"]
    assert client.get(f"/api/projects/{project['id']}").json()["plan"]["plan"]["id"] == second["id"]
    assert client.get("/api/workspace").json()["paused"] is False


def test_wait_returns_when_plan_needs_approval(app, monkeypatch, capsys):
    """An agent gets control back when the plan needs approval, without the
    monitor approving it or waiting out its whole timeout."""
    client, _ = app
    project = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    draft = client.post(f"/api/projects/{project['id']}/plan", json={
        "goal": "First iteration", "items": [{"title": "First task"}],
    }).raise_for_status().json()["plan"]
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)

    main(["--local", "plan-wait", draft["id"], "--timeout", "1", "--json"])

    observation = json.loads(capsys.readouterr().out)
    assert observation["reason"] == "needs_attention"
    assert observation["snapshot"]["plan"]["status"] == "draft"
    assert observation["snapshot"]["tasks"] == []


@pytest.mark.parametrize("paused", [False, True], ids=["no-capacity", "paused"])
def test_wait_distinguishes_capacity_from_operator_pause(app, monkeypatch, capsys, paused):
    """An approved queue with no runner reports capacity; an explicit pause
    instead returns control to its operator without dispatching or resuming."""
    client, _ = app
    _, plan = _approved_plan(client, monkeypatch)
    if paused:
        client.patch("/api/workspace", json={"paused": True}).raise_for_status()
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)

    main(["--local", "plan-wait", plan["id"], "--timeout", "1", "--json"])

    observation = json.loads(capsys.readouterr().out)
    assert observation["reason"] == ("needs_attention" if paused else "waiting_capacity")
    assert observation["snapshot"]["project_state"] == "blocked_resources"
    assert observation["snapshot"]["paused"] is paused
    assert all(task["status"] == "pending" for task in observation["snapshot"]["tasks"])


def test_wait_returns_at_deadline_without_interrupting_work(app, monkeypatch, capsys):
    """A healthy but unfinished plan returns a timed-out observation at the
    requested deadline; waiting never cancels tasks or changes plan state."""
    client, _ = app
    _register_usable_runner(client, backend="codex")
    _, plan = _approved_plan(client, monkeypatch)
    elapsed = [0.0]

    def sleep(seconds):
        elapsed[0] += seconds

    monkeypatch.setattr("hive.cli.time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=sleep))
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)

    main(["--local", "plan-wait", plan["id"], "--timeout", "0.25", "--json"])

    observation = json.loads(capsys.readouterr().out)
    assert observation["reason"] == "timed_out"
    assert observation["elapsed_seconds"] == pytest.approx(0.25)
    assert observation["snapshot"]["plan"]["status"] == "approved"
    assert observation["snapshot"]["project_state"] == "working"
    after = client.get(f"/api/plans/{plan['id']}").raise_for_status().json()
    assert after["items"] == observation["snapshot"]["items"]
    assert after["tasks"] == observation["snapshot"]["tasks"]


def test_wait_observes_build_review_and_landing_before_deadline(app, monkeypatch, capsys):
    """The monitor keeps observing its plan as the runner builds, reviews,
    and lands it, then returns the recorded merged commit as completion."""
    client, store = app
    monkeypatch.setattr("hive.api.merge_branch", lambda *args, **kwargs: "c" * 40)
    monkeypatch.setattr("hive.api.delete_branch", lambda *args, **kwargs: None)
    runner_id = _register_usable_runner(client, backend="codex")
    _, plan = _approved_plan(client, monkeypatch)
    elapsed = [0.0]
    observed_stages = []

    def finish_next_task(seconds):
        elapsed[0] += seconds
        _pump(client, store)
        task = client.post(f"/api/runners/{runner_id}/poll", headers=RUNNER_HEADERS).raise_for_status().json()["task"]
        observed_stages.append(task["kind"])
        text = "Built the feature.\nOUTCOME: FIXED" if task["kind"] == "resolve" else "Verified.\nREVIEW: ACCEPT"
        client.post(f"/api/tasks/{task['id']}/result", headers=RUNNER_HEADERS, json={"text": text}).raise_for_status()

    monkeypatch.setattr("hive.cli.time", SimpleNamespace(monotonic=lambda: elapsed[0], sleep=finish_next_task))
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)

    main(["--local", "plan-wait", plan["id"], "--timeout", "20", "--json"])

    observation = json.loads(capsys.readouterr().out)
    assert observation["reason"] == "completed"
    assert observation["elapsed_seconds"] < 20
    assert observed_stages == ["resolve", "review"]
    assert observation["snapshot"]["items"][0]["merged_commit_sha"] == "c" * 40


def test_wait_does_not_hide_parked_plan_behind_other_project_work(app, monkeypatch, capsys):
    """Another workstream can keep the project working while this particular
    plan is parked; the plan's own decision point must still wake its agent."""
    client, _ = app
    _register_usable_runner(client, backend="codex")
    project, plan = _approved_plan(client, monkeypatch)
    task = client.get(f"/api/plans/{plan['id']}").raise_for_status().json()["tasks"][0]
    client.post(f"/api/tasks/{task['id']}/cancel").raise_for_status()
    client.post(f"/api/projects/{project['id']}/directives", json={"text": "Other independent work"}).raise_for_status()
    monkeypatch.setattr("httpx.Client", lambda **kwargs: client)

    main(["--local", "plan-wait", plan["id"], "--timeout", "0.05", "--json"])

    observation = json.loads(capsys.readouterr().out)
    assert observation["snapshot"]["project_state"] == "working"
    assert observation["snapshot"]["items"][0]["status"] == "blocked_clarity"
    assert observation["reason"] == "needs_attention"


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_wait_rejects_unbounded_or_nonpositive_timeout(timeout):
    """Invalid durations fail at the CLI boundary before opening a chief
    connection, so an agent cannot accidentally request an unbounded wait."""
    with pytest.raises(SystemExit) as failure:
        main(["--local", "plan-wait", "a" * 12, "--timeout", timeout, "--json"])
    assert failure.value.code == 2
