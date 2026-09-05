"""Executable validation certifies a pushed commit, rather than agent claims."""

import subprocess

import pytest


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    path = tmp_path / "work"
    git(tmp_path, "clone", str(remote), str(path))
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "checkout", "-b", "hive/plan-test")
    (path / "value").write_text("good\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "Initial")
    git(path, "push", "origin", "HEAD")
    return path


def test_validation_certifies_only_clean_pushed_passing_commit(repo):
    """A real command must pass against the committed tree that GitHub can merge."""
    from hive.runner._validation import validate_checkout

    result = validate_checkout(repo, "test $(cat value) = good", "hive/plan-test")
    assert result.exit_code == 0
    assert result.commit_sha == git(repo, "rev-parse", "HEAD")

    failed = validate_checkout(repo, "echo 'test failed'; exit 2", "hive/plan-test")
    assert failed.exit_code == 2 and not failed.commit_sha
    assert "test failed" in failed.output

    (repo / "value").write_text("edited\n")
    dirty = validate_checkout(repo, "true", "hive/plan-test")
    assert dirty.exit_code != 0 and "uncommitted" in dirty.output
    git(repo, "commit", "-am", "Unpushed")
    unpushed = validate_checkout(repo, "true", "hive/plan-test")
    assert unpushed.exit_code != 0 and "push" in unpushed.output


def test_validation_rejects_test_side_effects_and_times_out(repo):
    """A passing command cannot certify files it changed or hold a runner forever."""
    from hive.runner._validation import validate_checkout

    changed = validate_checkout(repo, "echo changed > value", "hive/plan-test")
    assert changed.exit_code != 0 and not changed.commit_sha
    git(repo, "restore", "value")
    timeout = validate_checkout(repo, "sleep 10", "hive/plan-test", timeout_s=0.05)
    assert timeout.exit_code != 0 and "timed out" in timeout.output


@pytest.mark.parametrize("command", ["echo checks-passed", "echo checks-failed; exit 1", ""])
def test_review_runner_evidence_gates_landing(repo, tmp_path, monkeypatch, command):
    """Real runner execution and result processing merge only the tested commit.

    The empty case simulates an older runner omitting required evidence.
    Only the coding provider and remote merge transport are scripted.
    """
    from types import SimpleNamespace
    from hive.runner import _daemon as daemon
    from hive.runner._task_results import TaskResult
    from hive.models import Project, Task, TaskStatus, PlanItemStatus
    from hive.persistence.store import MemoryStore
    from hive._workstreams import plans
    from test_plans import activated_plan, only_resolve_task, report, make_processor

    store = MemoryStore()
    project = store.put(Project(name="validated", spec_repo=str(repo),
                                validation_command=command or "echo checks-passed"))
    plan = activated_plan(store, project)
    merged = []
    processor, _ = make_processor(store, tmp_path, merge=lambda repo, head, token, **kw: merged.append(head))
    report(store, processor, only_resolve_task(store, project), "OUTCOME: FIXED")
    review = store.list(Task, status=TaskStatus.pending)[0]
    assert review.validation_command == project.validation_command
    git(repo, "branch", "-m", review.branch)
    git(repo, "push", "origin", "HEAD")
    monkeypatch.setattr(daemon, "checkout", lambda *a, **kw: repo)
    monkeypatch.setattr(daemon, "_upload_trace", lambda *a: None)
    monkeypatch.setattr(daemon, "_upload_artifacts", lambda *a: None)
    monkeypatch.setattr(daemon, "refresh_usage", lambda *a: None)
    monkeypatch.setenv("KODO_RUNS_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(daemon, "run_agent", lambda *a, **kw: SimpleNamespace(
        text="REVIEW: ACCEPT", is_error=False, cost_usd=0, input_tokens=0, output_tokens=0,
        structured_result={}, structured_result_error="", session_handle="",
    ))
    payload = daemon.execute(review.model_dump(mode="json"), {}, None)
    if not command:
        payload.pop("validation", None)
    store.update(Task, review.id, lambda t: setattr(t, "status", TaskStatus.running))
    processor.handle(review.id, TaskResult(**payload), review.workspace_id)
    first, second, _ = plans.plan_items(store, plan)
    if command == "echo checks-passed":
        assert merged == [git(repo, "rev-parse", "HEAD")]
        assert first.status == PlanItemStatus.done and second.status == PlanItemStatus.resolving
        assert store.get(Task, review.id).validation.commit_sha == merged[0]
    else:
        assert not merged and second.status == PlanItemStatus.queued
        assert "validation" in first.parked_reason.lower()
