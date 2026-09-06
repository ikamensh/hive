"""Runtime/configuration faults are operational blocks, never project ambiguity."""

import pytest

from hive.agents import classify_failure
from hive._control.supervisor import Supervisor, state_reason
from hive._workstreams import plans
from hive.models import (
    AgentPreference, HumanTask, LimitEvent, PlanItemStatus, Project, Resource,
    ResourceUsability, Runner, Task, TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive.runner._task_results import TaskResult
from test_plans import make_processor


VERSION_ERROR = "API Error: 400 Claude Code 2.1.175 does not support this model; version 2.1.251 or newer required."
BUFFER_ERROR = (
    "Claude session error during query: CLIJSONDecodeError: Failed to decode JSON: "
    "JSON message exceeded maximum buffer size of 1048576 bytes..."
)


@pytest.mark.parametrize("fallback", ["codex", "opencode"])
def test_review_buffer_overflow_preserves_review_and_uses_another_provider(tmp_path, fallback):
    """Oversized SDK transport messages need a runtime repair, not a product
    rejection; a fresh configured reviewer continues on the same checkout."""
    store = MemoryStore()
    project = store.put(Project(
        name="dashboard", spec_repo="https://example.com/sim.git", included_only=True, daily_budget_usd=0,
        build_backend="opencode", build_model="opencode/muse-spark-1.3-contributor-free",
        review_backend="claude", review_model="claude-opus-5",
        agent_preferences=[AgentPreference(backend="claude", model="claude-opus-5"),
                           AgentPreference(backend="codex", model="gpt-6-astra"),
                           AgentPreference(backend="opencode", model="opencode/muse-spark-1.3-contributor-free")],
    ))
    runner = store.put(Runner(name="local", backends=["claude", "codex", "opencode"]))
    for backend in runner.backends:
        store.put(Resource(runner_id=runner.id, backend=backend, usability_status=ResourceUsability.usable,
                           enabled=backend != "codex" or fallback == "codex"))
    plan = plans.create_draft(store, project, "Build dashboard", [{"title": "Dashboard"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    supervisor = Supervisor(store, lambda *_: None)
    processor, _ = make_processor(store, tmp_path)
    processor.supervisor = supervisor
    assert supervisor.dispatch(project) == 1
    build = store.list(Task, status=TaskStatus.running)[0]
    processor.handle(build.id, TaskResult(text="OUTCOME: FIXED", session_handle="build-session"), "default")
    assert supervisor.dispatch(project) == 1
    review = store.list(Task, status=TaskStatus.running)[0]
    assert review.kind == "review" and review.backend == "claude" and review.session_handle == ""
    response = processor.handle(review.id, TaskResult(text=BUFFER_ERROR, is_error=True,
                                                      session_handle="oversized-review-session"), "default")
    assert response.get("requeued"), response
    failed = store.get(Task, review.id).model_dump()
    assert failed["status"] == "failed" and failed["verdict"] == "none"
    assert plans.plan_items(store, plan)[0].status == PlanItemStatus.reviewing
    resource = next(r for r in store.list(Resource) if r.backend == "claude")
    assert resource.usability_status == "failed" and resource.last_probe_text == BUFFER_ERROR
    assert resource.runtime_blocked_model == "claude-opus-5"
    assert resource.cooldown_until == 0 and resource.model_cooldowns == {} and store.list(LimitEvent) == []
    todo = store.list(HumanTask)[0]
    assert "max_buffer_size" in todo.instructions and BUFFER_ERROR in todo.instructions
    assert supervisor.dispatch(project) == 1
    successor = store.get(Task, response["retry_task_id"])
    assert successor.kind == "review" and successor.backend == fallback
    assert successor.id != review.id and successor.retry_of_task_id == review.id
    assert successor.preserve_checkout and not successor.fresh_branch
    assert successor.branch == review.branch and successor.runner_id == runner.id
    assert successor.session_handle == "" and "runtime" in successor.dispatch_reason
    processor.handle(review.id, TaskResult(text="Late output\nREVIEW: ACCEPT"), "default")
    assert store.get(Task, review.id).model_dump() == failed


@pytest.mark.parametrize("codex_available,expected", [(True, "codex"), (False, "opencode"), (False, None)])
def test_runtime_mismatch_keeps_work_queued_and_uses_another_provider(tmp_path, codex_available, expected):
    """A version fault retains the branch and phase, disables the incompatible
    runtime, records an actionable repair, and tries fresh Astra/Muse sessions."""
    store = MemoryStore()
    preferences = [AgentPreference(backend="claude", model="claude-fable-5-1"),
                   AgentPreference(backend="codex", model="gpt-6-astra"),
                   AgentPreference(backend="opencode", model="opencode/muse-spark-1.3-contributor-free")]
    project = store.put(Project(name="runtime", spec_repo="https://example.com/sim.git",
                                included_only=True, daily_budget_usd=0, agent_preferences=preferences))
    runner = store.put(Runner(name="local", backends=["claude", "codex", "opencode"]))
    for backend in runner.backends:
        store.put(Resource(runner_id=runner.id, backend=backend, usability_status=ResourceUsability.usable,
                           enabled=(backend != "codex" or codex_available) and (backend != "opencode" or expected is not None)))
    plan = plans.create_draft(store, project, "Build simulator", [{"title": "Implement cycle model"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    supervisor = Supervisor(store, lambda *_: None)
    processor, _ = make_processor(store, tmp_path)
    processor.supervisor = supervisor
    assert supervisor.dispatch(project) == 1
    original = store.list(Task, status=TaskStatus.running)[0]
    response = processor.handle(original.id, TaskResult(text=VERSION_ERROR, is_error=True,
                                                        session_handle="failed-claude-session"), project.workspace_id)
    assert response.get("requeued"), response
    assert classify_failure(VERSION_ERROR, is_error=True) == "runtime"
    assert plans.plan_items(store, plan)[0].status == PlanItemStatus.resolving
    claude = next(r for r in store.list(Resource) if r.backend == "claude")
    assert claude.usability_status == ResourceUsability.failed
    assert claude.runtime_blocked_model == original.model
    assert claude.last_probe_text == VERSION_ERROR
    assert claude.cooldown_until == 0 and claude.model_cooldowns == {} and claude.usage_windows == []
    assert store.list(LimitEvent) == []
    todo = store.list(HumanTask)[0]
    assert "runtime" in todo.title and VERSION_ERROR in todo.instructions
    assert "2.1.251" in todo.instructions
    assert supervisor.dispatch(project) == int(expected is not None)
    successor = store.get(Task, response["retry_task_id"])
    if expected is None:
        assert successor.status == TaskStatus.pending
        supervisor.refresh_state(project)
        project = store.get(Project, project.id)
        assert "runtime" in state_reason(store, project, supervisor.available_backends(), 0)
        return
    assert successor.id != original.id and successor.backend == expected
    assert "runtime" in successor.dispatch_reason
    assert successor.session_handle == "" and successor.preserve_checkout
    assert successor.branch == original.branch and successor.transient_retries == 0
    assert successor.resume_runner_id == runner.id


@pytest.mark.parametrize("backend,model,message", [
    ("claude", "claude-fable-5-1", VERSION_ERROR),
    ("opencode", "opencode/muse-spark-1.3-contributor-free",
     "OpenCode isolation preflight failed: agent.compaction uses a paid model; permission override rejected"),
])
def test_runtime_repair_reprobes_exact_model_and_closes_only_after_success(tmp_path, backend, model, message):
    """An older default model cannot clear an incompatible requested model;
    repair probes stay pinned and failed probes produce one runtime todo."""
    from hive.runner.registration import RunnerRegister, queue_probe, register

    store = MemoryStore()
    store.put(Project(name="configured", agent_grants=[{"backends": [backend], "sessions_per_day": 5}]))
    runner = store.put(Runner(name="local", backends=[backend]))
    resource = store.put(Resource(runner_id=runner.id, backend=backend,
                                  usability_status=ResourceUsability.usable))
    processor, _ = make_processor(store, tmp_path)
    task = store.put(Task(project_id="", workstream_id="", repo="probe:local", kind="probe",
                          instructions="Probe exact model", backend=backend, model=model,
                          status=TaskStatus.running, runner_id=runner.id))
    resource.last_probe_task_id = task.id
    store.put(resource)
    processor.handle(task.id, TaskResult(text=message, is_error=True), "default")
    assert classify_failure(message, is_error=True) == "runtime"
    assert len(store.list(HumanTask)) == 1
    assert store.list(HumanTask)[0].kind == "repair"
    resource = store.get(Resource, resource.id)
    probe, resource = queue_probe(store, resource, runner)
    assert probe.model == model
    processor.handle(probe.id, TaskResult(text=message, is_error=True), "default")
    assert len(store.list(HumanTask)) == 1
    resource = store.get(Resource, resource.id)
    assert resource.usability_status == "failed" and resource.runtime_blocked_model == model
    assert store.list(HumanTask)[0].status == "open"
    register(store, RunnerRegister(name=runner.name, backends=[backend], boot=True, auto_probe=True,
                                  discoveries=[{"name": backend, "status": "ok", "version": "updated"}]), "default")
    resource = store.get(Resource, resource.id)
    assert resource.runtime_blocked_model == model
    probe = store.get(Task, resource.last_probe_task_id)
    assert probe.model == model
    processor.handle(probe.id, TaskResult(text="Probe succeeded"), "default")
    resource = store.get(Resource, resource.id)
    assert resource.usability_status == "usable" and resource.runtime_blocked_model == ""
    assert store.list(HumanTask)[0].status == "done"
    assert store.list(LimitEvent) == []


def test_runtime_classifier_does_not_relabel_project_failures_or_success():
    """Only explicit operational diagnostics trigger resource quarantine."""
    assert classify_failure(VERSION_ERROR, is_error=False) == ""
    assert classify_failure(BUFFER_ERROR, is_error=False) == ""
    assert classify_failure("Test failed: JSON message exceeded maximum buffer size", is_error=True) == ""
    assert classify_failure("Claude session error during query: CLIJSONDecodeError: malformed JSON", is_error=True) == ""
    assert classify_failure("Test failed: the simulation model does not support this material", is_error=True) == ""
    assert classify_failure("Opus weekly limit reached", is_error=True) == "exhausted"


def test_late_successful_default_probe_cannot_clear_failed_explicit_model(tmp_path):
    """A previously requested generic probe may finish after a runtime fault;
    only success on the exact blocked model proves that fault repaired."""
    store = MemoryStore()
    runner = store.put(Runner(name="local", backends=["claude"]))
    task = store.put(Task(project_id="", workstream_id="", repo="probe:local", kind="probe",
                          instructions="Old default probe", backend="claude", model="",
                          status=TaskStatus.running, runner_id=runner.id))
    resource = store.put(Resource(runner_id=runner.id, backend="claude", last_probe_task_id=task.id,
                                  usability_status=ResourceUsability.failed,
                                  runtime_blocked_model="claude-fable-5-1", last_probe_text=VERSION_ERROR))
    processor, _ = make_processor(store, tmp_path)
    processor.handle(task.id, TaskResult(text="Default model probe succeeded"), "default")
    saved = store.get(Resource, resource.id)
    assert saved.usability_status == "failed" and saved.runtime_blocked_model == "claude-fable-5-1"
    assert saved.last_probe_text == VERSION_ERROR
