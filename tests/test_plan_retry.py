"""Manual retries must retain the work whose blocker the owner just resolved."""

import subprocess

import pytest

from hive._workstreams import plans
from hive.models import (
    PlanItem, PlanItemStatus, Project, Resource, ResourceUsability, Runner, Task, TaskKind, TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive.runner._daemon import checkout
from test_plans import activated_plan, only_resolve_task
from test_runner_protocol import H, make_client


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True,
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("dispatched", [False, True])
def test_cancel_before_delivery_edit_and_retry_creates_fresh_checkout(tmp_path, monkeypatch, dispatched):
    """An unstarted task owns no checkout; retry may safely leave the prior item's branch."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
        "commit", "--allow-empty", "-m", "seed")
    monkeypatch.setattr("hive.runner._daemon.WORKDIR", tmp_path / "work")
    path = checkout(str(origin), "hive/previous-item", fresh_branch=True)
    store = MemoryStore()
    project = store.put(Project(name="retry", spec_repo=str(origin)))
    activated_plan(store, project)
    previous = only_resolve_task(store, project)
    client = make_client(store)
    if dispatched:
        runner = store.put(Runner(name="original-runner", backends=[previous.backend]))
        store.put(Resource(runner_id=runner.id, backend=previous.backend,
                           usability_status=ResourceUsability.usable))
        assert client.app.state.supervisor.dispatch(project) == 1
        previous = store.get(Task, previous.id)
        assert previous.status == TaskStatus.running and previous.started_at
    assert not previous.delivered
    client.post(f"/api/tasks/{previous.id}/cancel").raise_for_status()
    cancelled = store.get(Task, previous.id)
    client.patch(f"/api/plan-items/{previous.work_item_id}",
                 json={"constraints": "Reject zero-contribution causes."}).raise_for_status()
    client.post(f"/api/plan-items/{previous.work_item_id}/retry").raise_for_status()
    successor = only_resolve_task(store, project)

    resumed = checkout(successor.repo, successor.branch, fresh_branch=successor.fresh_branch,
                       preserve=successor.preserve_checkout)
    assert resumed == path
    assert git(path, "branch", "--show-current").strip() == successor.branch
    assert successor.fresh_branch and not successor.preserve_checkout
    assert not successor.resume_runner_id and not successor.session_handle
    assert not successor.retry_of_task_id
    assert "Reject zero-contribution causes." in successor.instructions
    assert store.get(Task, previous.id) == cancelled


@pytest.mark.parametrize("stage", [TaskKind.resolve, TaskKind.review])
@pytest.mark.parametrize("cancel_undelivered_retry", [False, True])
def test_manual_retry_preserves_partial_work_and_uses_updated_document(
    tmp_path, monkeypatch, stage, cancel_undelivered_retry,
):
    """API retry keeps commits, index, dirty and new files, and immutable attempt history."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
        "commit", "--allow-empty", "-m", "seed")
    monkeypatch.setattr("hive.runner._daemon.WORKDIR", tmp_path / "work")
    store = MemoryStore()
    project = store.put(Project(name="retry", spec_repo=str(origin)))
    plan = activated_plan(store, project)
    previous = only_resolve_task(store, project)
    previous.kind = stage
    store.put(previous)
    runner = store.put(Runner(name="original-runner", backends=[previous.backend]))
    store.put(Resource(runner_id=runner.id, backend=previous.backend,
                       usability_status=ResourceUsability.usable))
    client = make_client(store)
    assert client.app.state.supervisor.dispatch(project) == 1
    delivered = client.post(f"/api/runners/{runner.id}/poll", headers=H).json()["task"]
    assert delivered["id"] == previous.id
    previous = store.get(Task, previous.id)
    assert previous.delivered
    path = checkout(previous.repo, previous.branch, fresh_branch=previous.fresh_branch)
    git(path, "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
        "commit", "--allow-empty", "-m", "unpushed progress")
    (path / "part.txt").write_text("staged")
    git(path, "add", "part.txt")
    (path / "part.txt").write_text("unstaged")
    (path / "new.txt").write_text("untracked")
    before = {"head": git(path, "rev-parse", "HEAD"),
              "index": git(path, "diff", "--cached"), "worktree": git(path, "diff"),
              "status": git(path, "status", "--porcelain")}

    client.post(f"/api/tasks/{previous.id}/cancel").raise_for_status()
    client.post(f"/api/tasks/{previous.id}/result", headers=H, json={
        "cancelled": True, "text": "Need the owner to confirm the temperature range.",
        "session_handle": "prior-session",
    }).raise_for_status()
    previous = store.get(Task, previous.id)
    assert previous.status == TaskStatus.cancelled
    item = store.get(PlanItem, previous.work_item_id)
    client.patch(f"/api/plan-items/{item.id}",
                 json={"constraints": "Use 220 to 260 degrees Celsius."}).raise_for_status()
    response = client.post(f"/api/plan-items/{item.id}/retry")
    response.raise_for_status()
    successor = only_resolve_task(store, project)
    if cancel_undelivered_retry:
        client.post(f"/api/tasks/{successor.id}/cancel").raise_for_status()
        unrun = store.get(Task, successor.id)
        client.patch(f"/api/plan-items/{item.id}", json={"notes": "Preserve the completed progress."}).raise_for_status()
        client.post(f"/api/plan-items/{item.id}/retry").raise_for_status()
        successor = only_resolve_task(store, project)
        assert store.get(Task, unrun.id) == unrun
        assert "Preserve the completed progress." in successor.instructions
    resumed = checkout(successor.repo, successor.branch, fresh_branch=successor.fresh_branch,
                       preserve=successor.preserve_checkout)

    assert resumed == path
    assert {"head": git(path, "rev-parse", "HEAD"),
            "index": git(path, "diff", "--cached"), "worktree": git(path, "diff"),
            "status": git(path, "status", "--porcelain")} == before
    assert (path / "new.txt").read_text() == "untracked"
    assert successor.resume_runner_id == previous.runner_id
    assert successor.retry_of_task_id == previous.id
    assert successor.session_handle == ""  # reread amended instructions in a fresh session
    assert "Use 220 to 260 degrees Celsius." in successor.instructions
    assert previous.result_text in successor.instructions
    assert store.get(Task, previous.id) == previous
    assert plans.plan_items(store, plan)[1].status == PlanItemStatus.queued
