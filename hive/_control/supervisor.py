"""Deterministic supervision: project state machine, task dispatch, orchestrator wakes.

No LLM here. The supervisor computes blocked/working states purely from store
facts, assigns pending tasks to capable runners (serialized per repo), and
wakes the orchestrator on events. Invariant: if a project is not blocked and
not idle, something must be running or the orchestrator must be thinking.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import socket
import threading
import time
from collections import defaultdict
from typing import Callable

from hive._control.escalation import escalate
from hive.models import (
    AgentConversation,
    ConversationStatus,
    DEFAULT_WORKSPACE_ID,
    HumanTask,
    HumanTaskStatus,
    Machine,
    OrchestratorRun,
    Project,
    ProjectState,
    Resource,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)

log = logging.getLogger("hive._control.supervisor")

RUNNER_OFFLINE_TASK_FAIL_S = 300.0
LEASE_TTL_S = 60.0  # renewed every tick (15s); a dead leader is superseded within a minute
PARALLEL_REPO_TASKS = (TaskKind.test_sweep, TaskKind.test_reproduce, TaskKind.test_judge)

# Dark-machine escalation: a machine that heartbeated recently but has now been
# silent past its availability class's threshold gets an operator todo (a dead
# laptop runner once went unnoticed for 9 days). Laptops sleep for hours as a
# matter of course; servers should never be quiet.
MACHINE_DARK_AFTER_S = {"laptop": 24 * 3600.0, "server": 4 * 3600.0}
MACHINE_DARK_DEFAULT_S = 24 * 3600.0
# Silent longer than this = retired, not broken: no todo. Keeps graveyard rows
# (old selftest machines, replaced hosts) from generating noise forever.
MACHINE_RETIRED_AFTER_S = 7 * 24 * 3600.0


def utc_day_start() -> float:
    """Epoch seconds for 00:00 UTC today — the daily-budget window boundary."""
    midnight = datetime.datetime.now(datetime.UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.timestamp()


def _capacity_key(backend: str, capabilities: list[str]) -> str:
    caps = ",".join(sorted(capabilities))
    return f"{backend}|{caps}" if caps else backend


def _task_capacity_key(task: Task) -> str:
    return _capacity_key(task.backend, task.required_capabilities)


def _serializes_repo(task: Task) -> bool:
    return task.kind not in PARALLEL_REPO_TASKS


def compute_state(
    project: Project,
    workstreams: list[Workstream],
    open_question_count: int,
    tasks: list[Task],
    available_backends: set[str],
    over_budget: bool = False,
    available_capacity: set[str] | None = None,
) -> ProjectState:
    if project.goal_complete:
        return ProjectState.idle_goal_complete
    running = [t for t in tasks if t.status == TaskStatus.running]
    pending = [t for t in tasks if t.status == TaskStatus.pending]
    if running:
        return ProjectState.working
    if pending:
        if over_budget:
            return ProjectState.blocked_budget
        capacity = available_capacity if available_capacity is not None else available_backends
        # Backend-aware: a pending task only counts as progressable if some
        # online runner offers its backend with available quota. Otherwise the
        # project is genuinely stuck on resources, not silently "working".
        if any(_task_capacity_key(t) in capacity for t in pending):
            return ProjectState.working
        return ProjectState.blocked_resources
    active = [
        w
        for w in workstreams
        if w.source != WorkstreamSource.issue and w.status == WorkstreamStatus.active
    ]
    if open_question_count and not active:
        return ProjectState.needs_attention
    if active:
        return ProjectState.blocked_budget if over_budget else ProjectState.working
    if any(
        w.source == WorkstreamSource.issue
        and w.status in (WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected)
        for w in workstreams
    ):
        return ProjectState.needs_attention
    if open_question_count:
        return ProjectState.needs_attention
    return ProjectState.idle


class Supervisor:
    """Owns the control loop. `orchestrate` is invoked (in a worker thread) with
    (project_id, events) whenever a project needs an orchestrator decision."""

    TICK_S = 15.0
    HEARTBEAT_MIN_INTERVAL_S = 600.0  # rate-limit decision wakes not driven by events
    CI_CHECK_INTERVAL_S = 300.0  # how often a ci_autofix project's CI is polled per repo
    TESTING_CHECK_INTERVAL_S = 900.0  # how often a testing_auto project's backlog is re-judged

    def __init__(
        self,
        store,
        orchestrate: Callable[[str, list[str]], None],
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        machine_name: str = "",
        ci_check: Callable[[str], None] | None = None,
        testing_check: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.orchestrate = orchestrate
        self.workspace_id = workspace_id
        # CI auto-fix poller (network: filed by production_app, None in tests). Takes
        # a project id, checks each repo's CI, files+queues a fix when red.
        self.ci_check = ci_check
        # Autonomous-testing poller (same shape): keeps a testing_auto project's
        # backlog aligned and swept inside its budget envelope.
        self.testing_check = testing_check
        machine = machine_name or socket.gethostname()
        self.holder = f"{machine}:{os.getpid()}"
        self._events: dict[str, list[str]] = defaultdict(list)
        self._wakeup = asyncio.Event()
        self._busy: set[str] = set()  # projects with an orchestrator invocation in flight
        self._last_heartbeat: dict[str, float] = {}
        self._last_ci_check: dict[str, float] = {}
        self._ci_busy: set[str] = set()  # projects with a CI check in flight
        self._last_testing_check: dict[str, float] = {}
        self._testing_busy: set[str] = set()  # projects with a testing check in flight
        self._dispatch_lock = threading.RLock()

    def wake(self, project_id: str, event: str) -> None:
        self._events[project_id].append(event)
        self._wakeup.set()

    def acquire_leadership(self) -> None:
        """Claim the single-chief lease or refuse to start. Two chiefs
        on one store would double-dispatch and double-wake."""
        owner = self.store.claim_leader(self.holder, LEASE_TTL_S, self.workspace_id)
        if owner != self.holder:
            raise RuntimeError(
                f"another chief ({owner}) holds the leader lease for workspace "
                f"{self.workspace_id} — "
                f"stop it or wait {LEASE_TTL_S:.0f}s for its lease to expire"
            )

    def release_leadership(self) -> bool:
        """Release this process's lease on graceful shutdown.

        Crashed or fenced-out processes still rely on the TTL path; this only
        clears the lease when the current holder still owns it.
        """
        return self.store.release_leader(self.holder, self.workspace_id)

    # -- state & dispatch (pure store operations, callable from anywhere) ----

    def available_backends(self) -> set[str]:
        """Backends an online runner currently offers with available quota."""
        online = {
            r.id for r in self.store.list(Runner, workspace_id=self.workspace_id) if r.online()
        }
        return {
            res.backend
            for res in self.store.list(Resource, workspace_id=self.workspace_id)
            if res.available() and res.runner_id in online
        }

    def available_capacity(self) -> set[str]:
        """Backend+capability combinations an online runner can execute."""
        online = {
            r.id for r in self.store.list(Runner, workspace_id=self.workspace_id) if r.online()
        }
        capacity: set[str] = set()
        for res in self.store.list(Resource, workspace_id=self.workspace_id):
            if not res.available() or res.runner_id not in online:
                continue
            capacity.add(_capacity_key(res.backend, []))
            for capability in ("browser", "docker"):
                if res.supports([capability]):
                    capacity.add(_capacity_key(res.backend, [capability]))
            if res.supports(["browser", "docker"]):
                capacity.add(_capacity_key(res.backend, ["browser", "docker"]))
        return capacity

    def spend_today(self, project_id: str) -> float:
        start = utc_day_start()
        tasks = sum(
            t.cost_usd
            for t in self.store.list(
                Task, workspace_id=self.workspace_id, project_id=project_id
            )
            if t.finished_at >= start
        )
        orchestrator = sum(
            r.cost_usd
            for r in self.store.list(
                OrchestratorRun, workspace_id=self.workspace_id, project_id=project_id
            )
            if r.created_at >= start
        )
        return tasks + orchestrator

    def over_budget(self, project: Project) -> bool:
        return project.daily_budget_usd > 0 and self.spend_today(project.id) >= project.daily_budget_usd

    def refresh_state(self, project: Project) -> ProjectState:
        conversation = (
            self.store.get(AgentConversation, project.intake_conversation_id)
            if project.intake_conversation_id
            else None
        )
        if conversation and conversation.status == ConversationStatus.done:
            pass
        elif conversation and conversation.status in (
            ConversationStatus.open,
            ConversationStatus.running,
            ConversationStatus.finalizing,
        ):
            if project.state != ProjectState.intake:
                project.state = ProjectState.intake
                self.store.put(project)
            return ProjectState.intake
        elif not project.goal_complete:
            workstreams = self.store.list(
                Workstream, workspace_id=self.workspace_id, project_id=project.id
            )
            tasks = [
                t
                for t in self.store.list(
                    Task, workspace_id=self.workspace_id, project_id=project.id
                )
                if t.kind not in (TaskKind.intake, TaskKind.probe)
            ]
            if not workstreams and not tasks:
                if project.state != ProjectState.intake:
                    project.state = ProjectState.intake
                    self.store.put(project)
                return ProjectState.intake
        workstreams = self.store.list(
            Workstream, workspace_id=self.workspace_id, project_id=project.id
        )
        tasks = [
            t
            for t in self.store.list(
                Task, workspace_id=self.workspace_id, project_id=project.id
            )
            if t.status in (TaskStatus.pending, TaskStatus.running)
        ]
        available_capacity = self.available_capacity()
        state = compute_state(
            project,
            workstreams,
            len(self.store.open_questions(project.id)),
            tasks,
            self.available_backends(),
            self.over_budget(project),
            available_capacity,
        )
        if state == ProjectState.blocked_resources:
            self._file_testing_capability_blocker(project, tasks, available_capacity)
        if state != project.state:
            project.state = state
            self.store.put(project)
        return state

    def _file_testing_capability_blocker(
        self,
        project: Project,
        tasks: list[Task],
        available_capacity: set[str],
    ) -> None:
        blocked = [
            task
            for task in tasks
            if task.status == TaskStatus.pending
            and task.required_capabilities
            and _task_capacity_key(task) not in available_capacity
        ]
        if not blocked:
            return
        needs = "\n".join(
            "- `{}` `{}` needs backend `{}` with {}".format(
                task.kind,
                task.id,
                task.backend,
                ", ".join(f"`{cap}`" for cap in sorted(task.required_capabilities)),
            )
            for task in blocked[:10]
        )
        available = ", ".join(f"`{item}`" for item in sorted(available_capacity)) or "(none)"
        escalate(
            self.store,
            f"Enable testing capabilities for {project.name}",
            instructions=(
                "Hive has testing tasks ready, but no online usable runner currently offers "
                "the required browser/Docker capability bundle.\n\n"
                f"Blocked task(s):\n{needs}\n\n"
                f"Available capacity right now: {available}\n\n"
                "Install and probe the missing runner capability, or run a capable runner, then "
                "mark this todo done so Hive can re-check dispatch."
            ),
            project_id=project.id,
            workspace_id=self.workspace_id,
        )

    def check_dark_machines(self) -> None:
        """File an operator todo for a machine that recently went dark, and
        close it again the moment the machine heartbeats. Runs every step —
        `escalate` is idempotent by title, so an outage yields one todo per
        machine per offline episode."""
        now = time.time()
        for machine in self.store.list(Machine, workspace_id=self.workspace_id):
            title = f"Bring machine {machine.name} back online"
            dark_for = now - machine.last_seen
            dark_after = MACHINE_DARK_AFTER_S.get(machine.device_kind, MACHINE_DARK_DEFAULT_S)
            if dark_for < dark_after:
                for task in self.store.list(
                    HumanTask, workspace_id=self.workspace_id, title=title, project_id=""
                ):
                    if task.status == HumanTaskStatus.open:
                        task.status = HumanTaskStatus.done
                        task.done_at = now
                        self.store.put(task)
            elif dark_for < MACHINE_RETIRED_AFTER_S:
                since = datetime.datetime.fromtimestamp(
                    machine.last_seen, datetime.UTC
                ).strftime("%Y-%m-%d %H:%M UTC")
                restart = (
                    "- macOS: `launchctl kickstart -k gui/$(id -u)/com.hive.runner`, "
                    "or re-run `bash deploy/install_mac_runner.sh` from the hive repo\n"
                    "- Linux: `deploy/vm.sh status`, then `sudo systemctl restart hive-runner`"
                )
                escalate(
                    self.store,
                    title,
                    instructions=(
                        f"Machine `{machine.name}` ({machine.device_kind}, "
                        f"{machine.machine_type or machine.os or 'unknown type'}) last "
                        f"heartbeated {since}. Its runners are offline, so their backends "
                        f"and capabilities are out of dispatch.\n\n{restart}\n\n"
                        "This todo closes itself when the machine reconnects."
                    ),
                    workspace_id=self.workspace_id,
                )

    def dispatch(self, project: Project) -> int:
        """Assign pending tasks to runners. Mutating tasks serialize per repo;
        test sweeps/confirmations are isolated by their own environments."""
        with self._dispatch_lock:
            return self._dispatch_unlocked(project)

    def _dispatch_unlocked(self, project: Project) -> int:
        if self.over_budget(project):
            return 0  # daily soft cap reached; no new spend until UTC midnight
        tasks = self.store.list(Task, workspace_id=self.workspace_id, project_id=project.id)
        busy_repos = {
            t.repo
            for t in tasks
            if t.status == TaskStatus.running and _serializes_repo(t)
        }
        busy_runners = {
            t.runner_id
            for t in tasks
            if t.status == TaskStatus.running and t.runner_id
        }
        runners = [r for r in self.store.list(Runner, workspace_id=self.workspace_id) if r.online()]
        resources = {
            (r.runner_id, r.backend): r
            for r in self.store.list(Resource, workspace_id=self.workspace_id)
            if r.available()
        }
        dispatched = 0
        for task in tasks:
            if task.status != TaskStatus.pending:
                continue
            if _serializes_repo(task) and task.repo in busy_repos:
                continue
            for runner in runners:
                if runner.id in busy_runners:
                    continue
                resource = resources.get((runner.id, task.backend))
                if (
                    task.backend in runner.backends
                    and resource is not None
                    and resource.supports(task.required_capabilities)
                ):
                    if self._claim(task.id, runner):
                        busy_runners.add(runner.id)
                        if _serializes_repo(task):
                            busy_repos.add(task.repo)
                        dispatched += 1
                        log.info("dispatched task %s to runner %s", task.id, runner.name)
                    break  # this task is decided (claimed, or taken by someone else)
        return dispatched

    def _claim(self, task_id: str, runner: Runner) -> bool:
        """Atomically move a still-pending task to running on this runner. Loses
        the race gracefully if it was cancelled or claimed concurrently."""
        claimed: list[bool] = []

        def claim(task: Task) -> None:
            if task.status == TaskStatus.pending:
                task.status = TaskStatus.running
                task.runner_id = runner.id
                task.started_at = time.time()
                claimed.append(True)

        self.store.update(Task, task_id, claim)
        return bool(claimed)

    def fail_orphaned_tasks(self) -> None:
        """Tasks running on runners that vanished come back as failures."""
        runners = {r.id: r for r in self.store.list(Runner, workspace_id=self.workspace_id)}
        for task in self.store.list(
            Task, workspace_id=self.workspace_id, status=TaskStatus.running
        ):
            runner = runners.get(task.runner_id)
            offline_s = time.time() - runner.last_seen if runner else float("inf")
            if offline_s <= RUNNER_OFFLINE_TASK_FAIL_S:
                continue
            failed: list[bool] = []

            def fail(t: Task) -> None:
                if t.status == TaskStatus.running:  # not already finished by a late result
                    t.status = TaskStatus.failed
                    t.is_error = True
                    t.result_text = f"Runner {t.runner_id} went offline mid-task."
                    t.finished_at = time.time()
                    failed.append(True)

            self.store.update(Task, task.id, fail)
            if failed:
                silent = "unknown" if runner is None else f"{offline_s:.0f}s"
                name = runner.name if runner else task.runner_id
                log.warning(
                    "runner %s (%s) silent for %s past %.0fs limit — failing orphaned "
                    "%s task %s on %s",
                    name, task.runner_id, silent, RUNNER_OFFLINE_TASK_FAIL_S,
                    task.kind, task.id, task.repo,
                )
                self.wake(task.project_id, f"Task {task.id} failed: runner went offline.")

    # -- loop -----------------------------------------------------------------

    async def run_forever(self) -> None:
        while True:
            owner = self.store.claim_leader(self.holder, LEASE_TTL_S, self.workspace_id)
            if owner != self.holder:
                # Fenced out: keeping the process alive would leave a second
                # API mutating tasks. Hard-exit so supervision restarts us cleanly.
                log.critical("lost leader lease to %s — exiting", owner)
                os._exit(1)
            try:
                await self._step()
            except Exception:
                log.exception("supervisor step failed")
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.TICK_S)
            except TimeoutError:
                pass
            self._wakeup.clear()

    def _ci_check_due(self, project: Project) -> bool:
        """A `ci_autofix` project whose per-poll interval has elapsed and which has
        no CI check already running. Pure gate so it is testable without a loop."""
        return (
            self.ci_check is not None
            and project.ci_autofix
            and project.id not in self._ci_busy
            and time.time() - self._last_ci_check.get(project.id, 0) > self.CI_CHECK_INTERVAL_S
        )

    async def _run_ci_check(self, project_id: str) -> None:
        try:
            await asyncio.to_thread(self.ci_check, project_id)
        except Exception:
            log.exception("CI auto-check failed for %s", project_id)
        finally:
            self._ci_busy.discard(project_id)
            self._wakeup.set()

    def _testing_check_due(self, project: Project) -> bool:
        """A `testing_auto` project inside its budget envelope whose poll interval
        elapsed and which has no testing check already running. Pure gate so it is
        testable without a loop (the per-action daily cooldown lives in
        `auto_testing_action` as store facts)."""
        return (
            self.testing_check is not None
            and project.testing_auto
            and project.daily_budget_usd > 0
            and project.id not in self._testing_busy
            and time.time() - self._last_testing_check.get(project.id, 0) > self.TESTING_CHECK_INTERVAL_S
        )

    async def _run_testing_check(self, project_id: str) -> None:
        try:
            await asyncio.to_thread(self.testing_check, project_id)
        except Exception:
            log.exception("autonomous testing check failed for %s", project_id)
        finally:
            self._testing_busy.discard(project_id)
            self._wakeup.set()

    async def _step(self) -> None:
        self.fail_orphaned_tasks()
        self.check_dark_machines()
        avail = self.available_backends()
        for project in self.store.list(Project, workspace_id=self.workspace_id):
            if project.archived or project.paused or not project.spec_repo.strip():
                continue
            if self._ci_check_due(project):
                self._last_ci_check[project.id] = time.time()
                self._ci_busy.add(project.id)
                asyncio.get_running_loop().create_task(self._run_ci_check(project.id))
            if self._testing_check_due(project) and not self.over_budget(project):
                self._last_testing_check[project.id] = time.time()
                self._testing_busy.add(project.id)
                asyncio.get_running_loop().create_task(self._run_testing_check(project.id))
            self.dispatch(project)
            state = self.refresh_state(project)
            if state == ProjectState.intake:
                # Intake is a runner-backed scout conversation. It can dispatch
                # tasks above, but it is not the build orchestrator's turn yet.
                continue
            if project.id in self._busy:
                continue  # invocation in flight; events stay queued for the next step
            if self.over_budget(project):
                # The orchestrator itself costs money. Leave events queued and go
                # quiet until spend rolls over at UTC midnight (dispatch is also
                # skipped), so a budgeted project can't be drained by replanning.
                continue
            events = self._events.pop(project.id, [])
            heartbeat_due = (
                time.time() - self._last_heartbeat.get(project.id, 0)
                > self.HEARTBEAT_MIN_INTERVAL_S
            )
            needs_decision = (
                state == ProjectState.working
                and not self.store.tasks_in(project.id, TaskStatus.running)
                and not self.store.tasks_in(project.id, TaskStatus.pending)
                and heartbeat_due
            )
            # Pending work is stuck on a backend no online runner offers, but
            # capacity exists elsewhere: nudge the orchestrator to replan onto
            # an available backend instead of waiting forever.
            replan = state == ProjectState.blocked_resources and bool(avail) and heartbeat_due
            if events or needs_decision or replan:
                if not events:
                    events = [self._replan_note(avail) if replan else self._heartbeat_note()]
                    self._last_heartbeat[project.id] = time.time()
                self._busy.add(project.id)
                asyncio.get_running_loop().create_task(self._orchestrate(project.id, events))

    @staticmethod
    def _heartbeat_note() -> str:
        return (
            "Heartbeat: workstreams are active but nothing is queued or running. "
            "Queue the next task, or park workstreams that are genuinely waiting."
        )

    @staticmethod
    def _replan_note(avail: set[str]) -> str:
        return (
            "Pending tasks cannot dispatch: no online runner offers their backend. "
            f"Available backends right now: {sorted(avail)}. Re-queue the next task on an "
            "available backend, or file a human task to bring the needed runner online."
        )

    async def _orchestrate(self, project_id: str, events: list[str]) -> None:
        try:
            await asyncio.to_thread(self.orchestrate, project_id, events)
        except Exception as exc:
            log.exception("orchestrator invocation failed for %s", project_id)
            self._file_orchestrator_failure(project_id, events, exc)
        finally:
            self._busy.discard(project_id)
            self._wakeup.set()

    def _file_orchestrator_failure(self, project_id: str, events: list[str], exc: Exception) -> None:
        project = self.store.get(Project, project_id)
        project_name = project.name if project else project_id
        title = f"Fix Hive orchestrator for {project_name}"
        detail = f"{type(exc).__name__}: {exc}"
        hint = ""
        detail_lower = detail.lower()
        if "api key" in detail_lower or "api_key" in detail_lower or "provider" in detail_lower:
            hint = (
                "\n\nThis often means the configured orchestrator credential is missing. "
                "Set `HIVE_ORCH_PROVIDER` plus `OPENAI_API_KEY` or `GEMINI_API_KEY`, "
                "and optionally set `HIVE_ORCH_MODEL`."
            )
        escalate(
            self.store,
            title,
            instructions=(
                "The supervisor tried to wake the LLM orchestrator, but the invocation failed before it "
                "could plan work.\n\n"
                f"Recent event(s):\n\n```\n{chr(10).join(events)[:1500]}\n```\n\n"
                f"Error:\n\n```\n{detail[:1500]}\n```"
                f"{hint}\n\n"
                "Fix the orchestrator configuration, then mark this todo done so Hive re-evaluates the project."
            ),
            project_id=project_id,
            workspace_id=self.workspace_id,
        )
