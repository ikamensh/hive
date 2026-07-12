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

from hive._control import allowances, pause
from hive._control.allowances import utc_day_start
from hive._control.escalation import escalate, resolve_open_todos
from hive.fleet import DEFAULT_LIVENESS, Liveness
from hive.models import (
    AgentConversation,
    ConversationStatus,
    DEFAULT_WORKSPACE_ID,
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    Machine,
    ISSUE_BLOCKED,
    OrchestratorRun,
    PLAN_ITEM_PARKED,
    Plan,
    PlanItem,
    PlanItemStatus,
    PlanStatus,
    Project,
    ProjectState,
    Question,
    QuestionStatus,
    Resource,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    IssueItem,
)

log = logging.getLogger("hive._control.supervisor")

RUNNER_OFFLINE_TASK_FAIL_S = 300.0
LEASE_TTL_S = 60.0  # renewed every tick (15s); a dead leader is superseded within a minute
PARALLEL_REPO_TASKS = (TaskKind.test_sweep, TaskKind.test_reproduce, TaskKind.test_judge)


def _capacity_key(backend: str, capabilities: list[str]) -> str:
    caps = ",".join(sorted(capabilities))
    return f"{backend}|{caps}" if caps else backend


def _task_capacity_key(task: Task) -> str:
    return _capacity_key(task.backend, task.required_capabilities)


def _serializes_repo(task: Task) -> bool:
    return task.kind not in PARALLEL_REPO_TASKS


def compute_state(
    project: Project,
    issue_items: list[IssueItem],
    open_question_count: int,
    tasks: list[Task],
    available_backends: set[str],
    over_budget: bool = False,
    available_capacity: set[str] | None = None,
    grant_blocked: set[str] | None = None,
    plan_status: PlanStatus | None = None,
    plan_items: list[PlanItem] | None = None,
) -> ProjectState:
    running = [t for t in tasks if t.status == TaskStatus.running]
    pending = [t for t in tasks if t.status == TaskStatus.pending]
    if running:
        return ProjectState.working
    if pending:
        if over_budget:
            return ProjectState.blocked_budget
        capacity = available_capacity if available_capacity is not None else available_backends
        blocked = grant_blocked or set()
        # Backend-aware: a pending task only counts as progressable if some
        # online runner offers its backend with available quota. Otherwise the
        # project is genuinely stuck on resources, not silently "working".
        with_capacity = [t for t in pending if _task_capacity_key(t) in capacity]
        if any(t.id not in blocked for t in with_capacity):
            return ProjectState.working
        if with_capacity:
            # Capacity exists but today's session allowance is spent — same
            # midnight-reset semantics as the money budget.
            return ProjectState.blocked_budget
        return ProjectState.blocked_resources
    items = plan_items or []
    if any(i.status in PLAN_ITEM_PARKED for i in items):
        # A parked item stalls the strict-order plan until the human acts.
        return ProjectState.needs_attention
    if plan_status == PlanStatus.draft:
        # A drafted plan awaiting the human's review is the project's next step.
        return ProjectState.needs_attention
    if plan_status == PlanStatus.approved and any(
        i.status == PlanItemStatus.queued for i in items
    ):
        return ProjectState.blocked_budget if over_budget else ProjectState.working
    if any(w.status in ISSUE_BLOCKED for w in issue_items):
        return ProjectState.needs_attention
    if open_question_count:
        return ProjectState.needs_attention
    if project.goal_complete:
        # The completion gate already ran in mark_goal_complete (plan complete,
        # nothing in flight, no open questions) — the flag is trustworthy.
        return ProjectState.idle_goal_complete
    return ProjectState.idle


def state_reason(
    store,
    project: Project,
    available_backends: set[str],
    spend: float,
) -> str:
    """One human sentence explaining `project.state`, with the fix attached.

    The user never has to translate internal state names: every badge ships
    with why-and-what-to-do. Reads the same store facts the state came from,
    so it can only drift from the badge by one refresh cycle."""
    if project.paused:
        return "paused by you — resume it to continue"
    state = project.state
    if state == ProjectState.intake:
        conv = (
            store.get(AgentConversation, project.intake_conversation_id)
            if project.intake_conversation_id
            else None
        )
        if conv is None:
            return "waiting on you: hand over the spec and start intake"
        if conv.status == ConversationStatus.running:
            return f"the intake scout ({conv.backend}) is reading and drafting — its brief lands in the intake panel"
        if conv.status == ConversationStatus.finalizing:
            return "intake approved — the scout is pushing the durable spec files"
        if conv.status == ConversationStatus.failed:
            return "intake failed — retry with another scout from the intake panel"
        return "waiting on you: answer or approve the scout's brief in the intake panel"
    if state == ProjectState.working:
        running = store.list(Task, project_id=project.id, status=TaskStatus.running)
        if running:
            doing = ", ".join(sorted({f"{t.kind} on {t.backend}" for t in running}))
            return f"{len(running)} task(s) running: {doing}"
        return "work is queued — dispatching to a machine now"
    if state == ProjectState.needs_attention:
        bits: list[str] = []
        open_questions = store.list(Question, project_id=project.id, status=QuestionStatus.open)
        if open_questions:
            bits.append(f"{len(open_questions)} question(s) to answer")
        live = [
            p
            for p in store.list(Plan, project_id=project.id)
            if p.status in (PlanStatus.draft, PlanStatus.approved)
        ]
        if live:
            plan = live[-1]
            if plan.status == PlanStatus.draft:
                bits.append("a drafted plan awaits your review")
            for item in store.list(PlanItem, plan_id=plan.id):
                if item.status in PLAN_ITEM_PARKED:
                    why = f": {item.parked_reason[:120]}" if item.parked_reason else ""
                    bits.append(f"plan item '{item.title}' is {item.status}{why}")
        blocked_issues = [
            w
            for w in store.list(IssueItem, project_id=project.id)
            if w.status in ISSUE_BLOCKED
        ]
        if blocked_issues:
            numbers = ", ".join(f"#{w.issue_number}" for w in blocked_issues[:5])
            bits.append(f"{len(blocked_issues)} issue(s) blocked on your call ({numbers})")
        return "needs you: " + "; ".join(bits) if bits else "needs you — see the project page"
    if state == ProjectState.blocked_resources:
        pending = store.list(Task, project_id=project.id, status=TaskStatus.pending)
        missing = sorted({t.backend for t in pending if t.backend not in available_backends})
        resources = [
            r
            for r in store.list(Resource, workspace_id=project.workspace_id)
            if r.backend in missing and r.cooldown_until > time.time()
        ]
        if resources:
            soonest = min(r.cooldown_until for r in resources)
            when = datetime.datetime.fromtimestamp(soonest, datetime.UTC).strftime("%H:%M UTC")
            return (
                f"out of quota for {', '.join(missing)} — resumes by itself around {when}"
            )
        if missing:
            return (
                f"waiting for capacity: no online machine offers {', '.join(missing)} — "
                "wake or log in a machine on the machines page"
            )
        return "waiting for agent capacity — check the machines page"
    if state == ProjectState.blocked_budget:
        if spend >= project.daily_budget_usd:
            return (
                f"daily budget spent (${spend:.2f} of ${project.daily_budget_usd:.2f}) — "
                "paid work resumes at UTC midnight; raise the budget to continue now"
            )
        return (
            "today's agent-session allowance is used up — resumes at UTC midnight; "
            "raise the allowance in settings to continue now"
        )
    if state == ProjectState.idle_goal_complete:
        return "iteration complete — read the completion note and set the next goal"
    return "idle — no live plan; ask Hive to propose one, or type an ask into the launchpad"


class Supervisor:
    """Owns the control loop. `orchestrate` is invoked (in a worker thread) with
    (project_id, events) whenever a project needs an orchestrator decision."""

    TICK_S = 15.0
    HEARTBEAT_MIN_INTERVAL_S = 600.0  # rate-limit decision wakes not driven by events
    CI_CHECK_INTERVAL_S = 300.0  # how often a ci_autofix project's CI is polled per repo
    TESTING_CHECK_INTERVAL_S = 900.0  # how often a testing_auto project's backlog is re-judged
    ISSUE_SCAN_INTERVAL_S = 600.0  # how often new GitHub issues are ingested per project
    TODO_TRIAGE_INTERVAL_S = 1800.0  # burst-guard on the LLM todo-board review

    def __init__(
        self,
        store,
        orchestrate: Callable[[str, list[str]], None],
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        machine_name: str = "",
        ci_check: Callable[[str], None] | None = None,
        testing_check: Callable[[str], None] | None = None,
        issue_scan: Callable[[str], None] | None = None,
        todo_triage: Callable[[], None] | None = None,
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
        # Unattended issue ingestion (same shape): new issues — external, testing
        # findings, directives — enter the resolve pipeline without a human scan.
        self.issue_scan = issue_scan
        # LLM second opinion on the open todo board (workspace-level, close-only;
        # see _control/todo_triage.py). Runs only when the board changed.
        self.todo_triage = todo_triage
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
        self._last_issue_scan: dict[str, float] = {}
        self._issue_scan_busy: set[str] = set()  # projects with an issue scan in flight
        self._last_todo_triage = 0.0
        self._triaged_board: tuple[str, ...] = ()  # open-todo ids at the last triage
        self._triage_busy = False
        self._dispatch_lock = threading.RLock()

    def wake(self, project_id: str, event: str) -> None:
        self._events[project_id].append(event)
        self._wakeup.set()

    def poke(self) -> None:
        """Run a step soon without queueing an orchestrator event — for changes
        (like a fleet resume) where dispatch should react but no LLM should."""
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
        """The daily budget caps every spender; 0 means paid work is paused."""
        return self.spend_today(project.id) >= project.daily_budget_usd

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
                IssueItem, workspace_id=self.workspace_id, project_id=project.id
            )
            tasks = [
                t
                for t in self.store.list(
                    Task, workspace_id=self.workspace_id, project_id=project.id
                )
                if t.kind not in (TaskKind.intake, TaskKind.probe)
            ]
            plans_exist = bool(
                self.store.list(Plan, workspace_id=self.workspace_id, project_id=project.id)
            )
            if not workstreams and not tasks and not plans_exist:
                if project.state != ProjectState.intake:
                    project.state = ProjectState.intake
                    self.store.put(project)
                return ProjectState.intake
        workstreams = self.store.list(
            IssueItem, workspace_id=self.workspace_id, project_id=project.id
        )
        # all_tasks feeds grant accounting (sessions_today needs finished tasks
        # and handles its own kind exemptions); compute_state sees live
        # non-bookkeeping work only.
        all_tasks = self.store.list(
            Task, workspace_id=self.workspace_id, project_id=project.id
        )
        tasks = [
            t
            for t in all_tasks
            if t.kind not in (TaskKind.intake, TaskKind.probe)
            and t.status in (TaskStatus.pending, TaskStatus.running)
        ]
        available_capacity = self.available_capacity()
        live_plans = [
            p
            for p in self.store.list(Plan, workspace_id=self.workspace_id, project_id=project.id)
            if p.status in (PlanStatus.draft, PlanStatus.approved)
        ]
        plan = live_plans[-1] if live_plans else None
        state = compute_state(
            project,
            workstreams,
            len(self.store.list(Question, project_id=project.id, status=QuestionStatus.open)),
            tasks,
            self.available_backends(),
            self.over_budget(project),
            available_capacity,
            self._grant_blocked(project, all_tasks),
            plan_status=plan.status if plan else None,
            plan_items=(
                self.store.list(PlanItem, workspace_id=self.workspace_id, plan_id=plan.id)
                if plan
                else None
            ),
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
                "Install and probe the missing runner capability, or run a capable runner. "
                "This todo closes itself when the project unblocks."
            ),
            project_id=project.id,
            workspace_id=self.workspace_id,
            kind=HumanTaskKind.infra,
            dedup_key=f"infra:capability:{project.id}",
            resolution={
                "check": "project_state_not",
                "project_id": project.id,
                "state": str(ProjectState.blocked_resources),
            },
        )

    def check_dark_machines(self) -> None:
        """File an operator todo for a machine that recently went dark. Runs
        every step — `escalate` is idempotent by dedup_key, so an outage yields
        one todo per machine per offline episode, and the todo's resolution
        predicate (a heartbeat newer than the todo) closes it on reconnect via
        the todo sweep."""
        now = time.time()
        for machine in self.store.list(Machine, workspace_id=self.workspace_id):
            verdict = DEFAULT_LIVENESS.assess(machine.last_seen, machine.device_kind, now=now)
            if verdict is Liveness.dark:
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
                    f"Bring machine {machine.name} back online",
                    instructions=(
                        f"Machine `{machine.name}` ({machine.device_kind}, "
                        f"{machine.machine_type or machine.os or 'unknown type'}) last "
                        f"heartbeated {since}. Its runners are offline, so their backends "
                        f"and capabilities are out of dispatch.\n\n{restart}\n\n"
                        "This todo closes itself when the machine reconnects."
                    ),
                    workspace_id=self.workspace_id,
                    assignee_user_id=machine.owner_user_id,
                    kind=HumanTaskKind.infra,
                    dedup_key=f"infra:machine:{machine.name}",
                    resolution={"check": "machine_online", "machine_name": machine.name},
                )

    def dispatch(self, project: Project) -> int:
        """Assign pending tasks to runners. Mutating tasks serialize per repo;
        test sweeps/confirmations are isolated by their own environments."""
        with self._dispatch_lock:
            return self._dispatch_unlocked(project)

    def _grant_blocked(self, project: Project, all_tasks: list[Task]) -> set[str]:
        """Pending task ids the project's agent allowance blocks right now."""
        grants = project.agent_grants
        if not grants:
            return set()
        left = allowances.remaining(
            grants, allowances.sessions_today(all_tasks, utc_day_start())
        )
        return {
            t.id
            for t in all_tasks
            if t.status == TaskStatus.pending
            and not allowances.exempt(t)
            and not allowances.admits(grants, left, t.backend, t.model)
        }

    def _dispatch_unlocked(self, project: Project) -> int:
        if pause.fleet_paused(self.store, self.workspace_id):
            return 0  # hive is paused: queued tasks wait, running ones finish
        if self.over_budget(project):
            return 0  # daily soft cap reached; no new spend until UTC midnight
        tasks = self.store.list(Task, workspace_id=self.workspace_id, project_id=project.id)
        # Session-allowance headroom, consumed locally as this pass dispatches.
        grants = project.agent_grants
        grant_left = allowances.remaining(
            grants, allowances.sessions_today(tasks, utc_day_start())
        )
        busy_repos = {
            t.repo
            for t in tasks
            if t.status == TaskStatus.running and _serializes_repo(t)
        }
        # Workspace-wide, not per-project: a runner executes one task at a time,
        # so stacking another project's task on it leaves other machines idle
        # while this one queues (observed live: raven ran one project's intake
        # with a second project's verify claimed behind it, hive-vm idle).
        busy_runners = {
            t.runner_id
            for t in self.store.list(
                Task, workspace_id=self.workspace_id, status=TaskStatus.running
            )
            if t.runner_id
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
            if (
                grants
                and not allowances.exempt(task)
                and not allowances.admits(grants, grant_left, task.backend, task.model)
            ):
                continue  # session allowance spent for this pair; waits for UTC midnight
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
                        if grants and not allowances.exempt(task):
                            allowances.consume(grants, grant_left, task.backend, task.model)
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

    def _issue_scan_due(self, project: Project) -> bool:
        """An unarchived project with the scan wired whose per-poll interval
        elapsed and which has no scan in flight. Pure gate, like the others."""
        return (
            self.issue_scan is not None
            and project.id not in self._issue_scan_busy
            and time.time() - self._last_issue_scan.get(project.id, 0) > self.ISSUE_SCAN_INTERVAL_S
        )

    async def _run_issue_scan(self, project_id: str) -> None:
        try:
            await asyncio.to_thread(self.issue_scan, project_id)
        except Exception:
            log.exception("issue scan failed for %s", project_id)
        finally:
            self._issue_scan_busy.discard(project_id)
            self._wakeup.set()

    def _open_todo_board(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                t.id
                for t in self.store.list(
                    HumanTask, workspace_id=self.workspace_id, status=HumanTaskStatus.open
                )
            )
        )

    def _todo_triage_due(self) -> bool:
        """The board review costs an LLM call, so it runs only when the set of
        open todos differs from what the last pass saw (and at most once per
        interval). A static board never re-triages; facts-only changes are the
        deterministic sweep's job."""
        return (
            self.todo_triage is not None
            and not self._triage_busy
            and time.time() - self._last_todo_triage > self.TODO_TRIAGE_INTERVAL_S
            and self._open_todo_board() != self._triaged_board
        )

    async def _run_todo_triage(self) -> None:
        try:
            await asyncio.to_thread(self.todo_triage)
        except Exception:
            log.exception("todo triage failed")
        finally:
            # Record the post-close board so the pass's own closes don't
            # immediately re-trigger it.
            self._triaged_board = self._open_todo_board()
            self._triage_busy = False
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
        resolve_open_todos(self.store, self.workspace_id)
        if pause.fleet_paused(self.store, self.workspace_id):
            # The master off-switch: bookkeeping above still ran, but nothing
            # below may start or spend — no triage/scans, no dispatch, no
            # orchestrator invocations. Queued events wait for the resume.
            return
        if self._todo_triage_due():
            self._last_todo_triage = time.time()
            self._triage_busy = True
            asyncio.get_running_loop().create_task(self._run_todo_triage())
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
            if self._issue_scan_due(project) and not self.over_budget(project):
                self._last_issue_scan[project.id] = time.time()
                self._issue_scan_busy.add(project.id)
                asyncio.get_running_loop().create_task(self._run_issue_scan(project.id))
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
                and not self.store.list(Task, project_id=project.id, status=TaskStatus.running)
                and not self.store.list(Task, project_id=project.id, status=TaskStatus.pending)
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
                "Fix the orchestrator configuration. This todo closes itself once the "
                "planner completes an invocation again (mark it done to re-evaluate sooner)."
            ),
            project_id=project_id,
            workspace_id=self.workspace_id,
            kind=HumanTaskKind.repair,
            dedup_key=f"repair:orchestrator:{project_id}",
            resolution={"check": "orchestrator_ran", "project_id": project_id},
        )
