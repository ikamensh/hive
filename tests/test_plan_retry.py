"""Manual retries must retain the work whose blocker the owner just resolved."""

import subprocess

import pytest

from hive._workstreams import plans
from hive.models import PlanItem, PlanItemStatus, Project, Task, TaskKind, TaskStatus
from hive.persistence.store import MemoryStore
from hive.runner._daemon import checkout
from test_plans import activated_plan, only_resolve_task
from test_runner_protocol import make_client


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True,
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("stage", [TaskKind.resolve, TaskKind.review])
def test_manual_retry_preserves_partial_work_and_uses_updated_document(tmp_path, monkeypatch, stage):
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
    previous.status = TaskStatus.done
    previous.runner_id = "original-runner"
    previous.result_text = "Need the owner to confirm the temperature range."
    store.put(previous)
    item = store.get(PlanItem, previous.work_item_id)
    item = plans.set_item_status(store, item.id, PlanItemStatus.blocked_clarity, previous.result_text)
    plans.update_item(store, item, {"constraints": "Use 220 to 260 degrees Celsius."})
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

    response = make_client(store).post(f"/api/plan-items/{item.id}/retry")
    response.raise_for_status()
    successor = only_resolve_task(store, project)
    resumed = checkout(successor.repo, successor.branch, fresh_branch=successor.fresh_branch,
                       preserve=successor.preserve_checkout)

    assert resumed == path
    assert {"head": git(path, "rev-parse", "HEAD"),
            "index": git(path, "diff", "--cached"), "worktree": git(path, "diff"),
            "status": git(path, "status", "--porcelain")} == before
    assert (path / "new.txt").read_text() == "untracked"
    assert successor.resume_runner_id == previous.runner_id
    assert successor.session_handle == ""  # reread amended instructions in a fresh session
    assert "Use 220 to 260 degrees Celsius." in successor.instructions
    assert previous.result_text in successor.instructions
    assert store.get(Task, previous.id) == previous
    assert plans.plan_items(store, plan)[1].status == PlanItemStatus.queued
