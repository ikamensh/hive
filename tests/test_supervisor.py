"""Supervisor state machine and dispatch properties.

The invariants tested here are the core promises of hive's deterministic
layer: state is computed purely from facts, dispatch serializes per repo,
and orphaned tasks fail instead of hanging forever.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time

from hive.models import (
    AgentConversation,
    ConversationStatus,
    HumanTask,
    OrchestratorRun,
    Project,
    ProjectState,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    Verdict,
    Workstream,
    WorkstreamStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.supervisor import Supervisor, compute_state


def make_supervisor(store) -> Supervisor:
    return Supervisor(store, orchestrate=lambda pid, events: None)


class SlowClaimSupervisor(Supervisor):
    def __init__(self, store) -> None:
        super().__init__(store, orchestrate=lambda pid, events: None)
        self._claim_test_lock = threading.Lock()
        self._active_claims = 0
        self.max_active_claims = 0

    def _claim(self, task_id, runner):
        with self._claim_test_lock:
            self._active_claims += 1
            self.max_active_claims = max(self.max_active_claims, self._active_claims)
        try:
            time.sleep(0.05)
            return super()._claim(task_id, runner)
        finally:
            with self._claim_test_lock:
                self._active_claims -= 1


class EmptyIdRejectingStore(MemoryStore):
    def get(self, model, id):
        if not id:
            raise AssertionError("empty document ids are invalid in Firestore")
        return super().get(model, id)


def seed(store, *, with_runner=True) -> Project:
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    if with_runner:
        runner = store.put(Runner(name="r1", backends=["cursor"]))
        store.put(Resource(runner_id=runner.id, backend="cursor",
                           usability_status=ResourceUsability.usable))
    return project


def test_goal_complete_only_when_project_is_quiescent_and_verified():
    p = Project(name="p", spec_repo="x", goal_complete=True)
    assert compute_state(p, [], 0, [], set()) == ProjectState.idle_goal_complete

    running = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i",
                   status=TaskStatus.running)
    assert compute_state(p, [], 0, [running], set()) == ProjectState.working

    pending = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i")
    assert compute_state(p, [], 0, [pending], {"cursor"}) == ProjectState.working

    assert compute_state(p, [], 1, [], set()) == ProjectState.needs_attention

    done_ws = Workstream(project_id=p.id, title="done", status=WorkstreamStatus.done)
    assert compute_state(p, [done_ws], 0, [], set()) == ProjectState.idle

    rejected_verify = Task(
        project_id=p.id,
        workstream_id=done_ws.id,
        repo="r",
        instructions="i",
        kind=TaskKind.verify,
        status=TaskStatus.done,
        verdict=Verdict.reject,
    )
    assert compute_state(p, [done_ws], 0, [rejected_verify], set()) == ProjectState.idle

    accepted_verify = Task(
        project_id=p.id,
        workstream_id=done_ws.id,
        repo="r",
        instructions="i",
        kind=TaskKind.verify,
        status=TaskStatus.done,
        verdict=Verdict.accept,
    )
    assert compute_state(p, [done_ws], 0, [accepted_verify], set()) == ProjectState.idle_goal_complete


def test_running_task_means_working():
    p = Project(name="p", spec_repo="x")
    t = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i",
             status=TaskStatus.running)
    assert compute_state(p, [], 5, [t], set()) == ProjectState.working


def test_pending_blocks_unless_backend_available():
    p = Project(name="p", spec_repo="x")
    t = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i", backend="claude")
    # A cursor-only fleet can't run a claude task: blocked, not fake-working.
    assert compute_state(p, [], 0, [t], {"cursor"}) == ProjectState.blocked_resources
    assert compute_state(p, [], 0, [t], {"claude"}) == ProjectState.working


def test_pending_over_budget_is_blocked_budget():
    p = Project(name="p", spec_repo="x")
    t = Task(project_id=p.id, workstream_id="w", repo="r", instructions="i")
    assert compute_state(p, [], 0, [t], {"cursor"}, over_budget=True) == ProjectState.blocked_budget


def test_questions_block_only_when_nothing_active():
    p = Project(name="p", spec_repo="x")
    active = Workstream(project_id=p.id, title="a")
    parked = Workstream(project_id=p.id, title="b", status=WorkstreamStatus.parked)
    assert compute_state(p, [parked], 2, [], set()) == ProjectState.needs_attention
    # An active workstream means the orchestrator owes a decision: still working.
    assert compute_state(p, [active, parked], 2, [], set()) == ProjectState.working
    # ...unless the daily budget is spent.
    assert compute_state(p, [active], 0, [], set(), over_budget=True) == ProjectState.blocked_budget


def test_no_workstreams_is_idle():
    p = Project(name="p", spec_repo="x")
    assert compute_state(p, [], 0, [], set()) == ProjectState.idle


def test_supervisor_holds_new_spec_project_in_intake_until_approved():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="x"))
    sup = make_supervisor(store)
    assert sup.refresh_state(project) == ProjectState.intake

    conversation = store.put(
        AgentConversation(
            project_id=project.id,
            repo=project.spec_repo,
            backend="codex",
            model="gpt-5.5",
            status=ConversationStatus.done,
        )
    )
    project.intake_conversation_id = conversation.id
    store.put(project)
    assert sup.refresh_state(project) == ProjectState.idle


def test_supervisor_does_not_fetch_empty_intake_conversation_id():
    store = EmptyIdRejectingStore()
    project = store.put(Project(name="p", spec_repo="x"))
    sup = make_supervisor(store)
    assert sup.refresh_state(project) == ProjectState.intake


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


def test_concurrent_dispatch_still_serializes_per_repo():
    store = MemoryStore()
    project = seed(store)
    runner = store.put(Runner(name="r2", backends=["cursor"]))
    store.put(Resource(runner_id=runner.id, backend="cursor",
                       usability_status=ResourceUsability.usable))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    for i in range(2):
        store.put(Task(project_id=project.id, workstream_id=ws.id,
                       repo="https://example.com/app.git", instructions=f"t{i}"))
    sup = SlowClaimSupervisor(store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sup.dispatch(project), range(2)))

    assert sorted(results) == [0, 1]
    assert sup.max_active_claims == 1
    assert len(store.list(Task, status=TaskStatus.running)) == 1
    assert len(store.list(Task, status=TaskStatus.pending)) == 1


def test_dispatch_parallel_across_repos():
    store = MemoryStore()
    project = seed(store)
    runner = store.put(Runner(name="r2", backends=["cursor"]))
    store.put(Resource(runner_id=runner.id, backend="cursor",
                       usability_status=ResourceUsability.usable))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="repo-a", instructions="a"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="repo-b", instructions="b"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 2


def test_dispatch_limits_parallel_test_tasks_to_runner_capacity():
    store = MemoryStore()
    project = seed(store)
    ws = store.put(Workstream(project_id=project.id, title="w"))
    for i in range(3):
        store.put(
            Task(
                project_id=project.id,
                workstream_id=ws.id,
                repo="same-repo",
                instructions=f"test {i}",
                kind=TaskKind.test_sweep,
            )
        )
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 1
    assert len(store.list(Task, status=TaskStatus.running)) == 1
    assert len(store.list(Task, status=TaskStatus.pending)) == 2

    runner = store.put(Runner(name="r2", backends=["cursor"]))
    store.put(Resource(runner_id=runner.id, backend="cursor",
                       usability_status=ResourceUsability.usable))
    assert sup.dispatch(project) == 1
    assert len(store.list(Task, status=TaskStatus.running)) == 2
    assert len(store.list(Task, status=TaskStatus.pending)) == 1


def test_dispatch_requires_backend_and_resource():
    store = MemoryStore()
    project = seed(store, with_runner=False)
    runner = store.put(Runner(name="r", backends=["claude"]))  # no resource row
    ws = store.put(Workstream(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                   instructions="i", backend="claude"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 0
    store.put(Resource(runner_id=runner.id, backend="claude",
                       usability_status=ResourceUsability.usable))
    assert sup.dispatch(project) == 1


def test_cooldown_resource_not_used():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="x"))
    runner = store.put(Runner(name="r", backends=["cursor"]))
    store.put(Resource(runner_id=runner.id, backend="cursor",
                       usability_status=ResourceUsability.usable,
                       cooldown_until=time.time() + 3600))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r", instructions="i"))
    sup = make_supervisor(store)
    assert sup.dispatch(project) == 0
    assert sup.refresh_state(project) == ProjectState.blocked_resources


def test_leader_lease_excludes_second_chief():
    store = MemoryStore()
    sup1, sup2 = make_supervisor(store), make_supervisor(store)
    sup2.holder = "other-host:1"  # same process → same default holder; force a distinct one
    sup1.acquire_leadership()
    sup1.acquire_leadership()  # renewal by the owner is fine
    try:
        sup2.acquire_leadership()
        raise AssertionError("second chief must be refused")
    except RuntimeError as exc:
        assert sup1.holder in str(exc)
    store._lease["expires"] = time.time() - 1  # leader died; lease lapsed
    sup2.acquire_leadership()


def test_graceful_leader_release_allows_immediate_restart():
    store = MemoryStore()
    sup1, sup2 = make_supervisor(store), make_supervisor(store)
    sup2.holder = "other-host:1"

    sup1.acquire_leadership()
    sup1.release_leadership()

    sup2.acquire_leadership()


def test_over_budget_blocks_dispatch_and_state():
    store = MemoryStore()
    project = seed(store, with_runner=True)
    project.daily_budget_usd = 1.0
    store.put(project)
    ws = store.put(Workstream(project_id=project.id, title="w"))
    # A task already finished today blew past the cap.
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r-done", instructions="i",
                   status=TaskStatus.done, cost_usd=2.0, finished_at=time.time()))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r-next", instructions="i"))
    sup = make_supervisor(store)
    assert sup.over_budget(project)
    assert sup.dispatch(project) == 0
    assert sup.refresh_state(project) == ProjectState.blocked_budget


def test_orchestrator_spend_counts_against_budget():
    store = MemoryStore()
    project = seed(store)
    project.daily_budget_usd = 1.0
    store.put(project)
    assert not make_supervisor(store).over_budget(project)
    # The planner's own LLM calls, with no runner tasks at all, can blow the cap.
    store.put(OrchestratorRun(project_id=project.id, model="gpt-5.5", cost_usd=1.5))
    sup = make_supervisor(store)
    assert sup.spend_today(project.id) == 1.5
    assert sup.over_budget(project)


def test_available_backends_tracks_online_and_cooldown():
    store = MemoryStore()
    online = store.put(Runner(name="on", backends=["cursor"]))
    offline = store.put(Runner(name="off", backends=["claude"], last_seen=time.time() - 9999))
    store.put(Resource(runner_id=online.id, backend="cursor",
                       usability_status=ResourceUsability.usable))
    store.put(Resource(runner_id=offline.id, backend="claude",
                       usability_status=ResourceUsability.usable))
    store.put(Resource(runner_id=online.id, backend="codex",
                       usability_status=ResourceUsability.usable,
                       cooldown_until=time.time() + 3600))
    store.put(Resource(runner_id=online.id, backend="gemini-cli"))
    sup = make_supervisor(store)
    assert sup.available_backends() == {"cursor"}  # offline + cooled-down + unprobed excluded


def test_orphaned_task_fails_when_runner_vanishes(caplog):
    store = MemoryStore()
    project = seed(store)
    runner = store.list(Runner)[0]
    runner.last_seen = time.time() - 9999
    store.put(runner)
    ws = store.put(Workstream(project_id=project.id, title="w"))
    task = store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                          instructions="i", status=TaskStatus.running, runner_id=runner.id))
    sup = make_supervisor(store)
    with caplog.at_level(logging.WARNING, logger="hive._control.supervisor"):
        sup.fail_orphaned_tasks()
    assert store.get(Task, task.id).status == TaskStatus.failed
    assert sup._events[project.id]  # orchestrator gets woken about it
    # The offline declaration is greppable in the chief log, not just a silent
    # Firestore mutation — this is the trail you follow to the runner's own logs.
    assert runner.id in caplog.text and "silent" in caplog.text


def test_orchestrator_failure_files_project_todo():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))

    def boom(_project_id, _events):
        raise RuntimeError("No API key was provided")

    sup = Supervisor(store, orchestrate=boom)
    asyncio.run(sup._orchestrate(project.id, ["Project created"]))
    tasks = store.list(HumanTask, project_id=project.id)
    assert len(tasks) == 1
    assert tasks[0].title == "Fix Hive orchestrator for p"
    assert "No API key was provided" in tasks[0].instructions

    asyncio.run(sup._orchestrate(project.id, ["Project created again"]))
    assert len(store.list(HumanTask, project_id=project.id)) == 1


def test_testing_capability_blocker_files_project_todo():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    conversation = store.put(
        AgentConversation(
            project_id=project.id,
            repo=project.spec_repo,
            backend="codex",
            model="gpt-5.5",
            status=ConversationStatus.done,
        )
    )
    project.intake_conversation_id = conversation.id
    store.put(project)
    runner = store.put(Runner(name="r1", backends=["codex"]))
    store.put(
        Resource(
            runner_id=runner.id,
            backend="codex",
            usability_status=ResourceUsability.usable,
        )
    )
    store.put(
        Task(
            project_id=project.id,
            workstream_id="story",
            repo="r",
            instructions="test",
            kind=TaskKind.test_sweep,
            backend="codex",
            required_capabilities=["browser", "docker"],
        )
    )

    sup = make_supervisor(store)
    assert sup.refresh_state(project) == ProjectState.blocked_resources
    todos = store.list(HumanTask, project_id=project.id)
    assert [t.title for t in todos] == ["Enable testing capabilities for p"]
    assert "`browser`" in todos[0].instructions
    assert "`docker`" in todos[0].instructions

    sup.refresh_state(project)
    assert len(store.list(HumanTask, project_id=project.id)) == 1


def test_dispatch_counts_running_tasks_across_projects():
    """A runner executes one task at a time, so a runner busy with *another*
    project's task must not be double-booked — the pending task should go to a
    free machine instead (observed live: two projects stacked on one laptop
    while the cloud server idled, 2026-07-05)."""
    store = MemoryStore()
    project_a = seed(store)  # r1/cursor
    runner2 = store.put(Runner(name="r2", backends=["cursor"]))
    store.put(Resource(runner_id=runner2.id, backend="cursor",
                       usability_status=ResourceUsability.usable))
    ws_a = store.put(Workstream(project_id=project_a.id, title="a"))
    project_b = store.put(Project(name="q", spec_repo="https://example.com/other.git"))
    ws_b = store.put(Workstream(project_id=project_b.id, title="b"))

    sup = make_supervisor(store)
    store.put(Task(project_id=project_a.id, workstream_id=ws_a.id,
                   repo="repo-a", instructions="a"))
    assert sup.dispatch(project_a) == 1
    busy_runner = store.list(Task, status=TaskStatus.running)[0].runner_id

    store.put(Task(project_id=project_b.id, workstream_id=ws_b.id,
                   repo="repo-b", instructions="b"))
    assert sup.dispatch(project_b) == 1
    b_task = next(t for t in store.list(Task, status=TaskStatus.running)
                  if t.project_id == project_b.id)
    assert b_task.runner_id != busy_runner  # went to the free runner

    # With every runner busy, a third task waits instead of stacking.
    project_c = store.put(Project(name="r", spec_repo="https://example.com/third.git"))
    ws_c = store.put(Workstream(project_id=project_c.id, title="c"))
    store.put(Task(project_id=project_c.id, workstream_id=ws_c.id,
                   repo="repo-c", instructions="c"))
    assert sup.dispatch(project_c) == 0


def test_issue_scan_due_gate():
    store = MemoryStore()
    sup = Supervisor(store, lambda p, e: None, issue_scan=lambda pid: None)
    project = store.put(Project(name="p", spec_repo="x"))

    assert sup._issue_scan_due(project)
    sup._last_issue_scan[project.id] = time.time()
    assert not sup._issue_scan_due(project)
    sup._last_issue_scan.pop(project.id)
    sup._issue_scan_busy.add(project.id)
    assert not sup._issue_scan_due(project)

    unwired = Supervisor(store, lambda p, e: None)
    assert not unwired._issue_scan_due(project)
