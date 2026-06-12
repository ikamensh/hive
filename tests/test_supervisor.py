"""Supervisor state machine and dispatch properties.

The invariants tested here are the core promises of hive's deterministic
layer: state is computed purely from facts, dispatch serializes per repo,
and orphaned tasks fail instead of hanging forever.
"""

import time

from hive.models import (
    Project,
    ProjectState,
    Resource,
    Runner,
    Task,
    TaskStatus,
    Workstream,
    WorkstreamStatus,
)
from hive.store import MemoryStore
from hive.supervisor import Supervisor, compute_state


def make_supervisor(store) -> Supervisor:
    return Supervisor(store, orchestrate=lambda pid, events: None)


def seed(store, *, with_runner=True) -> Project:
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    if with_runner:
        runner = store.put(Runner(name="r1", backends=["cursor"]))
        store.put(Resource(runner_id=runner.id, backend="cursor"))
    return project


def test_goal_complete_wins():
    p = Project(name="p", spec_repo="x", goal_complete=True)
    assert compute_state(p, [], 0, [], True) == ProjectState.idle_goal_complete


def test_running_task_means_working():
    p = Project(name="p", spec_repo="x")
    t = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i",
             status=TaskStatus.running)
    assert compute_state(p, [], 5, [t], False) == ProjectState.working


def test_pending_without_resources_is_blocked():
    p = Project(name="p", spec_repo="x")
    t = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i")
    assert compute_state(p, [], 0, [t], False) == ProjectState.blocked_resources
    assert compute_state(p, [], 0, [t], True) == ProjectState.working


def test_questions_block_only_when_nothing_active():
    p = Project(name="p", spec_repo="x")
    active = Workstream(project_id=p.id, title="a")
    parked = Workstream(project_id=p.id, title="b", status=WorkstreamStatus.parked)
    assert compute_state(p, [parked], 2, [], True) == ProjectState.blocked_questions
    # An active workstream means the orchestrator owes a decision: still working.
    assert compute_state(p, [active, parked], 2, [], True) == ProjectState.working


def test_no_workstreams_is_idle():
    p = Project(name="p", spec_repo="x")
    assert compute_state(p, [], 0, [], True) == ProjectState.idle_no_workstreams


def test_dispatch_serializes_per_repo():
    store = MemoryStore()
    project = seed(store)
    ws = store.put(Workstream(project_id=project.id, title="w"))
    for i in range(3):
        store.put(Task(project_id=project.id, workstream_id=ws.id,
                       repo="https://example.com/app.git", instructions=f"t{i}"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 1  # same repo: only one task starts
    running = store.list(Task, status=TaskStatus.running)
    assert len(running) == 1


def test_dispatch_parallel_across_repos():
    store = MemoryStore()
    project = seed(store)
    ws = store.put(Workstream(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="repo-a", instructions="a"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="repo-b", instructions="b"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 2


def test_dispatch_requires_backend_and_resource():
    store = MemoryStore()
    project = seed(store, with_runner=False)
    runner = store.put(Runner(name="r", backends=["claude"]))  # no resource row
    ws = store.put(Workstream(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                   instructions="i", backend="claude"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 0
    store.put(Resource(runner_id=runner.id, backend="claude"))
    assert sup.dispatch(project) == 1


def test_cooldown_resource_not_used():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="x"))
    runner = store.put(Runner(name="r", backends=["cursor"]))
    store.put(Resource(runner_id=runner.id, backend="cursor",
                       cooldown_until=time.time() + 3600))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r", instructions="i"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 0
    assert sup.refresh_state(project) == ProjectState.blocked_resources


def test_orphaned_task_fails_when_runner_vanishes():
    store = MemoryStore()
    project = seed(store)
    runner = store.list(Runner)[0]
    runner.last_seen = time.time() - 9999
    store.put(runner)
    ws = store.put(Workstream(project_id=project.id, title="w"))
    task = store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                          instructions="i", status=TaskStatus.running, runner_id=runner.id))
    sup = make_supervisor(store)
    sup.fail_orphaned_tasks()
    assert store.get(Task, task.id).status == TaskStatus.failed
    assert sup._events[project.id]  # orchestrator gets woken about it
