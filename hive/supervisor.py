"""Deterministic supervision: project state machine, task dispatch, orchestrator wakes.

No LLM here. The supervisor computes blocked/working states purely from store
facts, assigns pending tasks to capable runners (serialized per repo), and
wakes the orchestrator on events. Invariant: if a project is not blocked and
not idle, something must be running or the orchestrator must be thinking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Callable

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

log = logging.getLogger("hive.supervisor")

RUNNER_OFFLINE_TASK_FAIL_S = 300.0


def compute_state(
    project: Project,
    workstreams: list[Workstream],
    open_question_count: int,
    tasks: list[Task],
    any_resource_available: bool,
) -> ProjectState:
    if project.goal_complete:
        return ProjectState.idle_goal_complete
    running = [t for t in tasks if t.status == TaskStatus.running]
    pending = [t for t in tasks if t.status == TaskStatus.pending]
    if running:
        return ProjectState.working
    if pending:
        return ProjectState.working if any_resource_available else ProjectState.blocked_resources
    active = [w for w in workstreams if w.status == WorkstreamStatus.active]
    if open_question_count and not active:
        return ProjectState.blocked_questions
    if active:
        # Nothing queued but directions are open: the orchestrator owes a decision.
        return ProjectState.working
    if open_question_count:
        return ProjectState.blocked_questions
    return ProjectState.idle_no_workstreams


class Supervisor:
    """Owns the control loop. `orchestrate` is invoked (in a worker thread) with
    (project_id, events) whenever a project needs an orchestrator decision."""

    TICK_S = 15.0
    HEARTBEAT_MIN_INTERVAL_S = 600.0  # rate-limit decision wakes not driven by events

    def __init__(self, store, orchestrate: Callable[[str, list[str]], None]) -> None:
        self.store = store
        self.orchestrate = orchestrate
        self._events: dict[str, list[str]] = defaultdict(list)
        self._wakeup = asyncio.Event()
        self._busy: set[str] = set()  # projects with an orchestrator invocation in flight
        self._last_heartbeat: dict[str, float] = {}

    def wake(self, project_id: str, event: str) -> None:
        self._events[project_id].append(event)
        self._wakeup.set()

    # -- state & dispatch (pure store operations, callable from anywhere) ----

    def refresh_state(self, project: Project) -> ProjectState:
        workstreams = self.store.list(Workstream, project_id=project.id)
        tasks = [
            t
            for t in self.store.list(Task, project_id=project.id)
            if t.status in (TaskStatus.pending, TaskStatus.running)
        ]
        resources = [r for r in self.store.list(Resource) if r.available()]
        online = {r.id for r in self.store.list(Runner) if r.online()}
        any_available = any(r.runner_id in online for r in resources)
        state = compute_state(
            project, workstreams, len(self.store.open_questions(project.id)), tasks, any_available
        )
        if state != project.state:
            project.state = state
            self.store.put(project)
        return state

    def dispatch(self, project: Project) -> int:
        """Assign pending tasks to runners. One task per repo at a time."""
        tasks = self.store.list(Task, project_id=project.id)
        busy_repos = {t.repo for t in tasks if t.status == TaskStatus.running}
        runners = [r for r in self.store.list(Runner) if r.online()]
        resources = {
            (r.runner_id, r.backend): r for r in self.store.list(Resource) if r.available()
        }
        dispatched = 0
        for task in tasks:
            if task.status != TaskStatus.pending or task.repo in busy_repos:
                continue
            for runner in runners:
                if task.backend in runner.backends and (runner.id, task.backend) in resources:
                    task.status = TaskStatus.running
                    task.runner_id = runner.id
                    task.started_at = time.time()
                    self.store.put(task)
                    busy_repos.add(task.repo)
                    dispatched += 1
                    log.info("dispatched task %s to runner %s", task.id, runner.name)
                    break
        return dispatched

    def fail_orphaned_tasks(self) -> None:
        """Tasks running on runners that vanished come back as failures."""
        runners = {r.id: r for r in self.store.list(Runner)}
        for task in self.store.list(Task, status=TaskStatus.running):
            runner = runners.get(task.runner_id)
            offline_s = time.time() - runner.last_seen if runner else float("inf")
            if offline_s > RUNNER_OFFLINE_TASK_FAIL_S:
                task.status = TaskStatus.failed
                task.is_error = True
                task.result_text = f"Runner {task.runner_id} went offline mid-task."
                task.finished_at = time.time()
                self.store.put(task)
                self.wake(task.project_id, f"Task {task.id} failed: runner went offline.")

    # -- loop -----------------------------------------------------------------

    async def run_forever(self) -> None:
        while True:
            try:
                await self._step()
            except Exception:
                log.exception("supervisor step failed")
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.TICK_S)
            except TimeoutError:
                pass
            self._wakeup.clear()

    async def _step(self) -> None:
        self.fail_orphaned_tasks()
        for project in self.store.list(Project):
            if project.paused:
                continue
            self.dispatch(project)
            state = self.refresh_state(project)
            events = self._events.pop(project.id, [])
            needs_decision = (
                state == ProjectState.working
                and not self.store.tasks_in(project.id, TaskStatus.running)
                and not self.store.tasks_in(project.id, TaskStatus.pending)
                and time.time() - self._last_heartbeat.get(project.id, 0)
                > self.HEARTBEAT_MIN_INTERVAL_S
            )
            if (events or needs_decision) and project.id not in self._busy:
                if not events:
                    events = [
                        "Heartbeat: workstreams are active but nothing is queued or running. "
                        "Queue the next task, or park workstreams that are genuinely waiting."
                    ]
                    self._last_heartbeat[project.id] = time.time()
                self._busy.add(project.id)
                asyncio.get_running_loop().create_task(self._orchestrate(project.id, events))

    async def _orchestrate(self, project_id: str, events: list[str]) -> None:
        try:
            await asyncio.to_thread(self.orchestrate, project_id, events)
        except Exception:
            log.exception("orchestrator invocation failed for %s", project_id)
        finally:
            self._busy.discard(project_id)
            self._wakeup.set()
