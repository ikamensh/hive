"""Manual retries must retain the work whose blocker the owner just resolved."""

import subprocess

import pytest

from hive._workstreams import plans
from hive.models import (
    PlanItem, PlanItemStatus, Project, Resource, ResourceUsability, Runner, Task, TaskKind, TaskStatus,
)
from hive.persistence.store import FileStore, MemoryStore
from hive.runner._daemon import checkout
from test_plans import activated_plan, only_resolve_task
from test_runner_protocol import H, make_client


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def delivered_review(tmp_path):
    """A real build/result/poll cycle yields the delivered independent review."""
    store = FileStore(tmp_path / "store")
    project = store.put(Project(
        name="retry-review", spec_repo="https://example.com/dashboard.git",
        build_backend="codex", build_model="gpt-6-astra",
        review_backend="claude", review_model="claude-opus-5", validation_command="make check",
    ))
    plan = activated_plan(store, project)
    runner = store.put(Runner(name="original-runner", backends=["codex", "claude"]))
    for backend in runner.backends:
        store.put(Resource(runner_id=runner.id, backend=backend, usability_status=ResourceUsability.usable))
    client = make_client(store)
    assert client.app.state.supervisor.dispatch(project) == 1
    build = client.post(f"/api/runners/{runner.id}/poll", headers=H).raise_for_status().json()["task"]
    client.post(f"/api/tasks/{build['id']}/result", headers=H, json={
        "text": "Dashboard pushed.\nOUTCOME: FIXED", "session_handle": "build-session",
    }).raise_for_status()
    assert client.app.state.supervisor.dispatch(project) == 1
    review = client.post(f"/api/runners/{runner.id}/poll", headers=H).raise_for_status().json()["task"]
    assert review["kind"] == "review" and review["session_handle"] == ""
    return client, store, project, plan, runner, review


@pytest.mark.parametrize("validation", [
    pytest.param(None, id="no-gate"),
    pytest.param({"command": "make check", "exit_code": 0, "commit_sha": "a" * 40}, id="passing-gate"),
    pytest.param({"command": "make check", "exit_code": 0}, id="missing-validated-sha"),
    pytest.param({"command": "old check", "exit_code": 0, "commit_sha": "a" * 40}, id="old-gate-command"),
])
def test_manual_retry_resumes_failed_review_without_rebuilding(delivered_review, validation):
    """A transport failure has no product verdict: retry the review with a fresh
    session, its validation command and preserved checkout, not the build."""
    client, store, project, plan, runner, review = delivered_review
    client.post(f"/api/tasks/{review['id']}/result", headers=H, json={
        "text": "Claude session error during query: CLIJSONDecodeError: malformed JSON",
        "is_error": True, "session_handle": "failed-review-session", "validation": validation,
    }).raise_for_status()
    failed = store.get(Task, review["id"])
    assert failed.status == TaskStatus.failed and failed.verdict == "none"
    response = client.post(f"/api/plan-items/{review['work_item_id']}/retry").raise_for_status().json()
    assert response["status"] == "reviewing"
    pending = store.list(Task, status=TaskStatus.pending)
    assert len(pending) == 1
    successor = pending[0]
    assert successor.kind == TaskKind.review and successor.id != failed.id
    assert (successor.backend, successor.model) == ("claude", "claude-opus-5")
    assert successor.retry_of_task_id == failed.id and successor.resume_runner_id == runner.id
    assert successor.preserve_checkout and not successor.fresh_branch and not successor.session_handle
    assert successor.branch == failed.branch and successor.validation_command == "make check"
    assert set(successor.prompt_versions) == {"plan_review"}
    assert failed.result_text in successor.instructions
    assert store.get(Task, failed.id) == failed
    assert plans.plan_items(store, plan)[1].status == PlanItemStatus.queued


def test_review_retry_stage_survives_wait_restart_and_undelivered_cancellation(delivered_review, tmp_path):
    """Queued retry derives its phase from durable delivered history even after
    a new chief and cancellation of a successor that never owned the checkout."""
    client, store, project, plan, runner, review = delivered_review
    client.post(f"/api/tasks/{review['id']}/result", headers=H, json={
        "text": "Claude session error during query: CLIJSONDecodeError: malformed JSON",
        "is_error": True, "session_handle": "failed-review-session",
    }).raise_for_status()
    failed = store.get(Task, review["id"])
    # A separately parked item is persisted fleet state; it must prevent any
    # retry dispatch until the owner clears it, including across chief restart.
    other = plans.plan_items(store, plan)[1]
    plans.set_item_status(store, other.id, PlanItemStatus.blocked_clarity, "Separate owner decision")
    queued = client.post(f"/api/plan-items/{failed.work_item_id}/retry").raise_for_status().json()
    assert queued["status"] == "queued" and not store.list(Task, status=TaskStatus.pending)
    store = FileStore(tmp_path / "store")
    client = make_client(store)
    client.post(f"/api/plan-items/{other.id}/cancel").raise_for_status()
    (undelivered,) = store.list(Task, status=TaskStatus.pending)
    assert undelivered.kind == TaskKind.review and not undelivered.delivered
    client.post(f"/api/tasks/{undelivered.id}/cancel").raise_for_status()
    cancelled = store.get(Task, undelivered.id)
    client.patch(f"/api/plan-items/{failed.work_item_id}",
                 json={"notes": "Verify the completed dashboard with the repaired transport."}).raise_for_status()
    store = FileStore(tmp_path / "store")
    client = make_client(store)
    client.post(f"/api/plan-items/{failed.work_item_id}/retry").raise_for_status()
    (successor,) = store.list(Task, status=TaskStatus.pending)
    assert successor.kind == TaskKind.review and successor.retry_of_task_id == failed.id
    assert successor.preserve_checkout and successor.resume_runner_id == runner.id
    assert successor.session_handle == "" and successor.branch == failed.branch
    assert "Verify the completed dashboard with the repaired transport." in successor.instructions
    assert successor.validation_command == "make check" and set(successor.prompt_versions) == {"plan_review"}
    assert store.get(Task, failed.id) == failed and store.get(Task, cancelled.id) == cancelled
    assert client.app.state.supervisor.dispatch(project) == 1
    delivered = client.post(f"/api/runners/{runner.id}/poll", headers=H).raise_for_status().json()["task"]
    assert delivered["id"] == successor.id and delivered["kind"] == "review"
    late = client.post(f"/api/tasks/{failed.id}/result", headers=H,
                       json={"cancelled": True, "text": "Late cancellation acknowledgement"}).raise_for_status().json()
    assert late["ignored"] and store.get(Task, failed.id) == failed
    assert plans.plan_items(store, plan)[2].status == PlanItemStatus.queued


@pytest.mark.parametrize(("text", "is_error", "exit_code"), [
    pytest.param("Independent case failed.\nREVIEW: REJECT", False, 0, id="product-rejection-passing-gate"),
    pytest.param("Review finished.\nREVIEW: ACCEPT", False, 1, id="accepted-review-failing-gate"),
    pytest.param("Review transport failed after a failing gate", True, 1, id="transport-failure-failing-gate"),
])
def test_manual_retry_still_builds_after_product_or_validation_rejection(delivered_review, text, is_error, exit_code):
    """Actual product/gate failures require builder repair, even when an
    additional transport error prevented the reviewer from completing."""
    client, store, project, plan, runner, review = delivered_review
    for _ in range(plans.REPAIR_LIMIT + 1):
        client.post(f"/api/tasks/{review['id']}/result", headers=H, json={
            "text": text, "is_error": is_error,
            "validation": {"command": "make check", "exit_code": exit_code, "commit_sha": "b" * 40},
        }).raise_for_status()
        if store.get(PlanItem, review["work_item_id"]).status == PlanItemStatus.rejected:
            break
        assert client.app.state.supervisor.dispatch(project) == 1
        build = client.post(f"/api/runners/{runner.id}/poll", headers=H).raise_for_status().json()["task"]
        assert build["kind"] == "resolve"
        client.post(f"/api/tasks/{build['id']}/result", headers=H,
                    json={"text": "Repair pushed.\nOUTCOME: FIXED"}).raise_for_status()
        assert client.app.state.supervisor.dispatch(project) == 1
        review = client.post(f"/api/runners/{runner.id}/poll", headers=H).raise_for_status().json()["task"]
    previous = store.get(Task, review["id"])
    client.post(f"/api/plan-items/{review['work_item_id']}/retry").raise_for_status()
    successor = only_resolve_task(store, project)
    assert successor.retry_of_task_id == previous.id and successor.preserve_checkout
    assert successor.resume_runner_id == runner.id and not successor.session_handle
    assert set(successor.prompt_versions) == {"plan_resolve"}
    assert store.get(Task, previous.id) == previous
    assert plans.plan_items(store, plan)[1].status == PlanItemStatus.queued


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
