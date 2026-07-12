"""Runner task-result processing.

The HTTP API accepts a result; this module owns what that result means for each
task kind. Keeping the workflow transitions here leaves the route layer as
transport and auth glue instead of a mixed dispatcher/state machine.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from hive.runner._agent_results import (
    test_repro_outcome as structured_test_repro_outcome,
    test_sweep_outcome as structured_test_sweep_outcome,
    test_ux_outcome as structured_test_ux_outcome,
    verdict_from_structured,
)
from hive.agents import REGISTRY
from hive.config.settings import Config
from hive._control.escalation import (
    escalate,
    resolve_open_todos,
    resolve_todo,
    runner_machine_owner,
)
from hive._control.limits import (
    apply_snapshot,
    cooldown_after_exhaustion,
    record_exhaustion,
    record_snapshot,
)
from hive._workstreams.issues import (
    LANDING_FAILED_PREFIX,
    MergeConflictError,
    advance_issues,
    create_landing_integration_task,
    create_review_task,
    delete_branch as default_delete_branch,
    issue_branch,
    issue_is_closed as default_issue_is_closed,
    merge_branch as default_merge_branch,
    refresh_issue_run,
    resolve_issue_on_github as default_resolve_issue_on_github,
    sync_directives_for_issue,
)
from hive.models import (
    AgentConversation,
    ConversationStatus,
    DirectiveStatus,
    Finding,
    FindingStatus,
    HumanTask,
    HumanTaskKind,
    IssueRun,
    Plan,
    PlanItem,
    PlanItemStatus,
    PlanStatus,
    Project,
    ProjectState,
    ProjectWorkstream,
    Resource,
    ResourceUsability,
    Runner,
    Story,
    StoryFidelity,
    StoryStatus,
    Task,
    TaskKind,
    TaskStatus,
    TestabilityContract,
    TestEpisode,
    TestEpisodeStatus,
    TestReproOutcome,
    TestSweepOutcome,
    TestUxOutcome,
    Verdict,
    IssueItem,
    IssueItemStatus,
    parse_resolve,
    parse_review,
    parse_test_refresh,
    parse_test_repro,
    parse_test_sweep,
    parse_test_ux,
    parse_testability_draft,
    parse_testability_probe,
)
from hive._integrations.specrepo import SpecRepo
from hive._workstreams import plans
from hive._workstreams.testing import (
    close_issue as default_close_issue,
    file_or_update_finding_issue as default_file_or_update_finding_issue,
    finding_quality_problem,
    finalize_refresh,
    persist_sweep_findings,
    queue_confirm_task,
    refresh_episode_counts,
    result_payload as test_payload,
)
from hive._workstreams.testability import (
    DraftResultSummary,
    create_decision_questions,
    get_contract,
    queue_probe_task,
    reconcile_contract,
    record_probe_result,
)

log = logging.getLogger("hive.runner._task_results")

HUMAN_FIX_PATTERNS = re.compile(
    r"auth|login|credential|api.?key|not authenticated|forbidden|permission|subscription|billing",
    re.IGNORECASE,
)
ISSUE_RESULT_MARKER_RE = re.compile(
    r"^\s*(OUTCOME|REVIEW)\s*:\s*(FIXED|BLOCKED|ACCEPT|REJECT)\s*$",
    re.IGNORECASE,
)
ISSUE_COMMENT_SECTION_LIMIT = 6000
TEST_TASK_KINDS = (
    TaskKind.test_refresh,
    TaskKind.test_sweep,
    TaskKind.test_reproduce,
    TaskKind.test_judge,
    TaskKind.testability_draft,
    TaskKind.testability_probe,
)
LANDING_INTEGRATION_PROMPT = "landing_integration"


class TaskResult(BaseModel):
    text: str
    is_error: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    structured_result: dict = Field(default_factory=dict)
    structured_result_error: str = ""
    resource_exhausted: bool = False  # rate limit / quota detected by runner
    auth_blocked: bool = False  # login/policy block detected by runner (needs a human)
    cancelled: bool = False  # runner stopped the task on an operator cancel request
    session_handle: str = ""  # backend session id/chat id for conversation continuation
    reset_at_hint: float = 0.0  # limit-reset epoch the runner parsed from the error message
    usage_snapshot: dict = Field(default_factory=dict)  # provider usage windows after the task


def _structured_or_legacy_verdict(
    kind: TaskKind,
    body: TaskResult,
    legacy: Verdict,
) -> Verdict:
    structured = verdict_from_structured(kind, body.structured_result)
    return structured if structured != Verdict.none else legacy


def _test_refresh_done(body: TaskResult) -> bool:
    return (
        verdict_from_structured(TaskKind.test_refresh, body.structured_result) == Verdict.accept
        or parse_test_refresh(body.text)
    )


def _test_sweep_payload(body: TaskResult) -> dict:
    return body.structured_result or test_payload(body.text)


def _test_sweep_outcome(body: TaskResult) -> TestSweepOutcome:
    structured = structured_test_sweep_outcome(body.structured_result)
    return structured if structured != TestSweepOutcome.none else parse_test_sweep(body.text)


def _test_repro_outcome(body: TaskResult) -> TestReproOutcome:
    structured = structured_test_repro_outcome(body.structured_result)
    return structured if structured != TestReproOutcome.none else parse_test_repro(body.text)


def _test_ux_outcome(body: TaskResult) -> TestUxOutcome:
    structured = structured_test_ux_outcome(body.structured_result)
    return structured if structured != TestUxOutcome.none else parse_test_ux(body.text)


def _set_ws_status(store, ws_id: str, status: IssueItemStatus, reason: str) -> IssueItem | None:
    def mutate(ws: IssueItem) -> None:
        ws.status = status
        ws.parked_reason = reason

    return store.update(IssueItem, ws_id, mutate)


def cancel_issue_work(store, task: Task) -> None:
    if task.kind in (TaskKind.resolve, TaskKind.review) and task.workstream_id:
        _set_ws_status(
            store,
            task.workstream_id,
            IssueItemStatus.queued,
            "cancelled by operator — scan to retry",
        )


def sync_landing_failure_human_task(
    store,
    task: HumanTask,
    config: Config,
    *,
    issue_is_closed_func: Callable[..., bool] = default_issue_is_closed,
) -> None:
    match = re.fullmatch(r"Land issue #(\d+) failed", task.title)
    if not match or not task.project_id:
        return
    project = store.get(Project, task.project_id)
    if not project or not project.spec_repo:
        return
    issue_number = int(match.group(1))
    try:
        closed = issue_is_closed_func(project.spec_repo, issue_number, config.gh_token)
    except Exception as exc:
        log.warning("could not verify issue #%s while completing human task %s: %s", issue_number, task.id, exc)
        return
    if not closed:
        return
    for ws in store.list(IssueItem, project_id=project.id):
        if (
            ws.issue_number == issue_number
            and ws.status == IssueItemStatus.rejected
            and ws.parked_reason.startswith(LANDING_FAILED_PREFIX)
        ):
            _set_ws_status(store, ws.id, IssueItemStatus.done, "")
            log.info("human task %s confirmed issue #%s is closed; marked workstream done", task.id, issue_number)
            return


def _is_merge_conflict(exc: Exception) -> bool:
    return isinstance(exc, MergeConflictError) or "merge conflict" in str(exc).lower()


class TaskResultProcessor:
    def __init__(
        self,
        store,
        supervisor,
        config: Config,
        *,
        merge_branch_func: Callable[..., None] = default_merge_branch,
        resolve_issue_func: Callable[..., None] = default_resolve_issue_on_github,
        delete_branch_func: Callable[..., None] = default_delete_branch,
        file_finding_issue_func: Callable[..., tuple[int, str]] = default_file_or_update_finding_issue,
        close_issue_func: Callable[..., None] = default_close_issue,
    ) -> None:
        self.store = store
        self.supervisor = supervisor
        self.config = config
        self.merge_branch = merge_branch_func
        self.resolve_issue_on_github = resolve_issue_func
        self.delete_branch = delete_branch_func
        self.file_or_update_finding_issue = file_finding_issue_func
        self.close_issue = close_issue_func

    def handle(self, task_id: str, body: TaskResult, workspace_id: str) -> dict:
        result = self._handle(task_id, body, workspace_id)
        # A task result is exactly the kind of event that flips todo-resolution
        # facts (a probe went usable, a story reached a verdict, a work item
        # landed) — sweep now so the todo closes with the result instead of a
        # supervisor tick later.
        resolve_open_todos(self.store, workspace_id)
        return result

    def _handle(self, task_id: str, body: TaskResult, workspace_id: str) -> dict:
        existing = self.store.get(Task, task_id)
        if not existing or existing.workspace_id != workspace_id:
            raise LookupError(task_id)

        finished_at = time.time()
        recorded: list[bool] = []

        def record(task: Task) -> None:
            if task.status != TaskStatus.running:
                return
            if body.cancelled:
                task.status = TaskStatus.cancelled
            else:
                task.status = TaskStatus.failed if body.is_error else TaskStatus.done
            self._record_verdict(task, body)
            task.result_text = body.text
            task.is_error = body.is_error
            task.cost_usd = body.cost_usd
            task.input_tokens = body.input_tokens
            task.output_tokens = body.output_tokens
            task.structured_result = body.structured_result
            task.structured_result_error = body.structured_result_error
            task.finished_at = finished_at
            recorded.append(True)

        task = self.store.update(Task, task_id, record)
        if task is None:
            raise LookupError(task_id)
        if not recorded:
            return {"ok": True, "ignored": True, "status": task.status}

        probe_resources = self._account_resources(workspace_id, task, body)
        if task.kind == TaskKind.probe:
            self._handle_probe_result(task, body, probe_resources, workspace_id)
            return {"ok": True}

        if task.kind == TaskKind.intake and task.conversation_id:
            self._handle_intake_result(task, body)
            return {"ok": True}

        # A credential block on real work (not just a probe) proves the backend is
        # dead for everything on this resource. `_account_resources` already marked
        # it failed so dispatch stops; tell the operator how to fix it too.
        if body.auth_blocked:
            self._escalate_backend_login(task, body.text, workspace_id)

        # Plan items ride the same resolve/review kinds as issue work; which
        # record `work_item_id` resolves to is the discriminator.
        if task.kind in (TaskKind.resolve, TaskKind.review) and task.work_item_id:
            plan_item = self.store.get(PlanItem, task.work_item_id)
            if plan_item is not None:
                self._handle_plan_task_result(task, body, plan_item)
                return {"ok": True}

        if task.kind == TaskKind.resolve and not body.cancelled:
            self._land_resolve(task, body)
        elif task.kind == TaskKind.review and not body.cancelled:
            self._land_review(task, body)
        elif task.kind in (TaskKind.resolve, TaskKind.review) and body.cancelled:
            cancel_issue_work(self.store, task)
            self._refresh_run(task)

        if (
            task.kind in (TaskKind.resolve, TaskKind.review)
            and not body.cancelled
            and self._should_advance_after_issue_result(task)
        ):
            self._advance_after_issue_result(task)

        if task.kind in (TaskKind.resolve, TaskKind.review, TaskKind.preflight):
            return {"ok": True}

        if task.kind in TEST_TASK_KINDS:
            self._handle_test_task_result(task, body)
            return {"ok": True}

        self._wake_default(task, body)
        return {"ok": True}

    def _record_verdict(self, task: Task, body: TaskResult) -> None:
        if body.cancelled or body.is_error:
            return
        if task.kind == TaskKind.resolve:
            task.verdict = _structured_or_legacy_verdict(
                task.kind,
                body,
                parse_resolve(body.text),
            )
        elif task.kind == TaskKind.review:
            task.verdict = _structured_or_legacy_verdict(
                task.kind,
                body,
                parse_review(body.text),
            )
        elif task.kind == TaskKind.test_refresh:
            task.verdict = Verdict.accept if _test_refresh_done(body) else Verdict.none
        elif task.kind == TaskKind.test_sweep:
            task.verdict = (
                Verdict.accept
                if _test_sweep_outcome(body) == TestSweepOutcome.passed
                else Verdict.reject
            )
        elif task.kind == TaskKind.test_reproduce:
            task.verdict = (
                Verdict.accept
                if _test_repro_outcome(body) == TestReproOutcome.confirmed
                else Verdict.reject
            )
        elif task.kind == TaskKind.test_judge:
            task.verdict = (
                Verdict.accept
                if _test_ux_outcome(body) == TestUxOutcome.improvable
                else Verdict.reject
            )
        elif task.kind == TaskKind.testability_draft:
            task.verdict = _structured_or_legacy_verdict(
                task.kind,
                body,
                parse_testability_draft(body.text),
            )
        elif task.kind == TaskKind.testability_probe:
            task.verdict = _structured_or_legacy_verdict(
                task.kind,
                body,
                parse_testability_probe(body.text),
            )

    def _account_resources(
        self,
        workspace_id: str,
        task: Task,
        body: TaskResult,
    ) -> list[Resource]:
        probe_resources: list[Resource] = []
        # Set inside the store.update mutation, read after it: whether the
        # snapshot moved usage materially (worth a history event).
        snapshot_moved = False

        def account(resource: Resource) -> None:
            nonlocal snapshot_moved
            resource.total_tasks += 1
            resource.total_cost_usd += body.cost_usd
            snapshot_moved = apply_snapshot(resource, body.usage_snapshot)
            if task.kind == TaskKind.probe and resource.last_probe_task_id == task.id:
                resource.last_probe_at = task.finished_at
                resource.last_probe_text = body.text[:2000]
                if body.cancelled:
                    resource.usability_status = ResourceUsability.unknown
                elif body.auth_blocked:
                    resource.usability_status = ResourceUsability.failed
                elif body.resource_exhausted:
                    resource.usability_status = ResourceUsability.usable
                elif body.is_error:
                    resource.usability_status = ResourceUsability.failed
                else:
                    resource.usability_status = ResourceUsability.usable
                    resource.clear_exhaustion()
            elif body.auth_blocked:
                # A login/policy block on any task (not just a probe) proves the
                # credential is broken for all work on this resource. Mark it
                # failed so dispatch stops choosing it until a re-probe proves the
                # human fixed the login; clear any stale cooldown that would
                # otherwise let it silently look "usable" again. The failure text
                # becomes the latest usability evidence — otherwise `hive show`
                # keeps quoting the long-gone successful probe as the reason.
                resource.usability_status = ResourceUsability.failed
                resource.clear_exhaustion()
                resource.last_probe_at = task.finished_at
                resource.last_probe_text = body.text[:2000]
            if body.resource_exhausted:
                resource.mark_exhausted(
                    until=cooldown_after_exhaustion(
                        resource,
                        reset_at_hint=body.reset_at_hint,
                        snapshot=body.usage_snapshot,
                    ),
                    at=task.finished_at,
                    text=body.text,
                    task_id=task.id,
                )

        for resource in self.store.list(
            Resource,
            workspace_id=workspace_id,
            runner_id=task.runner_id,
            backend=task.backend,
        ):
            updated = self.store.update(Resource, resource.id, account)
            if updated is None:
                continue
            # History rows are written outside the update mutation: FirestoreStore
            # runs `account` inside a transaction, which must stay write-free.
            if snapshot_moved:
                record_snapshot(self.store, updated, body.usage_snapshot, task_id=task.id)
            if body.resource_exhausted:
                record_exhaustion(
                    self.store,
                    updated,
                    at=task.finished_at,
                    text=body.text,
                    reset_at_hint=body.reset_at_hint,
                    task_id=task.id,
                )
            if task.kind == TaskKind.probe and updated.last_probe_task_id == task.id:
                probe_resources.append(updated)
        return probe_resources

    def _handle_probe_result(
        self,
        task: Task,
        body: TaskResult,
        probe_resources: list[Resource],
        workspace_id: str,
    ) -> None:
        if (
            any(resource.enabled for resource in probe_resources)
            and body.is_error
            and not body.resource_exhausted
            and HUMAN_FIX_PATTERNS.search(body.text)
        ):
            self._escalate_backend_login(task, body.text, workspace_id)

    def _escalate_backend_login(self, task: Task, text: str, workspace_id: str) -> None:
        """File (idempotently) the operator todo to repair a backend's broken
        login/billing on the machine that hit the block, so a dead credential
        surfaces in the inbox instead of silently failing every task it touches.
        Filed from both the probe path and any real task that hits an auth block."""
        runner = self.store.get(Runner, task.runner_id)
        runner_name = runner.name if runner else task.runner_id
        hint = REGISTRY.get(task.backend).login_hint if task.backend in REGISTRY else ""
        escalate(
            self.store,
            f"Fix {task.backend} login on {runner_name}",
            instructions=(
                f"Refresh or repair the `{task.backend}` CLI login on runner "
                f"`{runner_name}`, then rerun the resource probe."
                f"{chr(10) + chr(10) + hint if hint else ''}\n\n"
                f"Recent output:\n\n```\n{text[:1500]}\n```"
            ),
            workspace_id=workspace_id,
            assignee_user_id=runner_machine_owner(self.store, runner),
            kind=HumanTaskKind.access,
            dedup_key=f"access:{task.backend}:{runner_name}",
            resolution={
                "check": "resource_usable",
                "backend": task.backend,
                "runner_name": runner_name,
            },
        )

    def _handle_intake_result(self, task: Task, body: TaskResult) -> None:
        def update_conversation(conversation: AgentConversation) -> None:
            conversation.updated_at = task.finished_at
            if body.session_handle.strip():
                conversation.session_handle = body.session_handle.strip()
            if body.cancelled:
                conversation.status = ConversationStatus.open
                conversation.transcript.append({"role": "system", "text": "Intake turn cancelled."})
                return
            if body.is_error:
                conversation.status = ConversationStatus.failed
                conversation.latest_brief = body.text
                conversation.transcript.append({"role": "assistant", "text": body.text})
                return
            conversation.latest_brief = body.text
            conversation.transcript.append({"role": "assistant", "text": body.text})
            conversation.status = (
                ConversationStatus.done
                if task.conversation_turn == "finalize"
                else ConversationStatus.open
            )

        conversation = self.store.update(AgentConversation, task.conversation_id, update_conversation)
        project = self.store.get(Project, task.project_id)
        if project and conversation:
            if not body.is_error and not body.cancelled:
                # A retry mints a new conversation, so no store fact ties the
                # failed one to recovery — the successful turn is the event.
                resolve_todo(
                    self.store,
                    project.workspace_id,
                    f"repair:intake:{project.id}",
                    "a later intake turn succeeded",
                )
            if conversation.status == ConversationStatus.done:
                conversation = self._verify_finalized_spec(task, body, project, conversation)
            if conversation.status == ConversationStatus.done:
                project.state = ProjectState.idle
                self.store.put(project)
                self.supervisor.wake(
                    task.project_id,
                    f"Intake accepted and pushed by scout task {task.id}. Plan from the durable spec.\n"
                    f"Result:\n{body.text[:6000]}",
                )
            elif conversation.status == ConversationStatus.open:
                project.state = ProjectState.intake
                self.store.put(project)
            elif conversation.status == ConversationStatus.failed:
                self._escalate_intake_failure(task, body, project)

    def _verify_finalized_spec(
        self, task: Task, body: TaskResult, project: Project, conversation: AgentConversation
    ) -> AgentConversation:
        """Trust but verify: a finalize turn only counts when the durable spec
        files actually exist on the remote. Live regression (gleaner,
        2026-07-05): the scout committed locally, the push 403'd, the turn
        reported success — and planning woke on an empty spec repo."""
        from hive._control.intake import spec_status

        status = spec_status(self.config, project)
        if status.ready:
            return conversation
        problem = status.error or f"missing files: {', '.join(status.missing_files)}"
        note = (
            f"Finalize reported success, but the spec repo does not verify: {problem}. "
            "Intake stays open — fix the blocker (often push access) and approve again."
        )

        def reopen(conv: AgentConversation) -> None:
            conv.status = ConversationStatus.open
            conv.transcript.append({"role": "system", "text": note})

        log.warning("intake finalize for %s did not land: %s", project.name, problem)
        escalate(
            self.store,
            f"Intake finalize did not land for {project.name}",
            instructions=(
                f"The intake scout for **{project.name}** reported a successful finalize, "
                f"but the spec repo does not verify: {problem}\n\n"
                f"Scout report tail:\n\n```\n{body.text[-800:]}\n```\n\n"
                "Usually this is missing push access for Hive's GitHub identity on the "
                "spec repo. Fix that, then approve intake again."
            ),
            project_id=project.id,
            workspace_id=project.workspace_id,
            kind=HumanTaskKind.repair,
            dedup_key=f"repair:intake-finalize:{project.id}",
            resolution={"check": "conversation_done", "conversation_id": conversation.id},
        )
        return self.store.update(AgentConversation, conversation.id, reopen) or conversation

    def _escalate_intake_failure(self, task: Task, body: TaskResult, project: Project) -> None:
        """Intake hit a wall. File an operator todo so the project isn't a silent
        dead-end. An auth/policy block shares the failed probe's dedup key, so a
        later successful re-probe auto-closes it; other failures resolve when a
        later intake turn succeeds."""
        runner = self.store.get(Runner, task.runner_id) if task.runner_id else None
        runner_name = runner.name if runner else (task.runner_id or "the runner")
        hint = REGISTRY[task.backend].login_hint if task.backend in REGISTRY else ""
        detail = (body.text or "").strip()[:1500]
        if body.auth_blocked:
            title = f"Fix {task.backend} login on {runner_name}"
            instructions = (
                f"The `{task.backend}` intake scout for **{project.name}** was blocked by a "
                f"login/policy error on runner `{runner_name}`:\n\n```\n{detail}\n```\n\n"
                f"{hint + chr(10) + chr(10) if hint else ''}"
                "Hive marked that backend unusable and will re-check it on the next probe. "
                "You can also retry intake with a different trusted scout from the project setup."
            )
            kind = HumanTaskKind.access
            dedup_key = f"access:{task.backend}:{runner_name}"
            resolution = {
                "check": "resource_usable",
                "backend": task.backend,
                "runner_name": runner_name,
            }
        else:
            title = f"Intake scout failed for {project.name}"
            instructions = (
                f"The `{task.backend}` intake scout for **{project.name}** failed:\n\n"
                f"```\n{detail}\n```\n\n"
                "Retry intake from the project setup (optionally with a different trusted scout)."
            )
            kind = HumanTaskKind.repair
            dedup_key = f"repair:intake:{project.id}"
            resolution = {}  # closed by the next successful intake turn (event above)
        escalate(
            self.store,
            title,
            instructions=instructions,
            kind=kind,
            dedup_key=dedup_key,
            resolution=resolution,
            project_id="" if body.auth_blocked else project.id,
            workspace_id=project.workspace_id,
            assignee_user_id=runner_machine_owner(self.store, runner) if body.auth_blocked else "",
        )

    def _land_resolve(self, task: Task, body: TaskResult) -> None:
        if body.is_error:
            log.warning(
                "resolve task %s (issue #%s) errored; leaving 'resolving' for re-scan retry: %s",
                task.id,
                task.issue_number,
                body.text[:300],
            )
            return
        if task.verdict == Verdict.none:
            log.warning(
                "resolve task %s (issue #%s) finished WITHOUT an `OUTCOME:` line — "
                "treating as BLOCKED. Tail: %s",
                task.id,
                task.issue_number,
                body.text[-300:],
            )

        def transition(ws: IssueItem) -> None:
            if ws.status != IssueItemStatus.resolving:
                return
            if task.verdict == Verdict.accept:
                ws.status = IssueItemStatus.reviewing
                ws.parked_reason = ""
            else:
                ws.status = IssueItemStatus.blocked_clarity
                ws.parked_reason = "blocked at clarify step — see the GitHub issue comment"

        ws = self.store.update(IssueItem, task.workstream_id, transition)
        if ws is None:
            return
        log.info(
            "resolve task %s (issue #%s) verdict=%s → workstream %s",
            task.id,
            task.issue_number,
            task.verdict,
            ws.status,
        )
        if ws.status == IssueItemStatus.reviewing:
            project = self.store.get(Project, task.project_id)
            if project:
                run = self.store.get(IssueRun, task.run_id) if task.run_id else None
                review = create_review_task(
                    self.store,
                    project,
                    ws,
                    backend=task.backend,
                    model=task.model,
                    run=run,
                )
                if run:
                    refresh_issue_run(self.store, project, run)
                log.info("queued review task %s for issue #%s on %s", review.id, ws.issue_number, review.branch)

    def _refresh_run(self, task: Task) -> None:
        """Recompute the parent issue run's aggregate status after a per-issue
        transition. No-op for tasks not tied to a run."""
        if not task.run_id:
            return
        project = self.store.get(Project, task.project_id)
        run = self.store.get(IssueRun, task.run_id)
        if project and run:
            refresh_issue_run(self.store, project, run)

    def _land_review(self, task: Task, body: TaskResult) -> None:
        ws = self.store.get(IssueItem, task.workstream_id)
        if ws is None or ws.status != IssueItemStatus.reviewing:
            return
        landing_integration = self._is_landing_integration_task(task)
        if body.is_error:
            log.warning(
                "review task %s (issue #%s) errored → rejected (re-scan to retry): %s",
                task.id,
                task.issue_number,
                body.text[:300],
            )
            reason = (
                f"{LANDING_FAILED_PREFIX}: landing integration errored — re-scan to retry"
                if landing_integration
                else "review errored — re-scan to retry"
            )
            _set_ws_status(self.store, ws.id, IssueItemStatus.rejected, reason)
            self._refresh_run(task)
            return
        if task.verdict == Verdict.none:
            log.warning(
                "review task %s (issue #%s) finished WITHOUT a `REVIEW:` line — treating as REJECT. "
                "Tail: %s",
                task.id,
                task.issue_number,
                body.text[-300:],
            )
        if task.verdict != Verdict.accept:
            log.info("review task %s (issue #%s) verdict=%s → rejected", task.id, task.issue_number, task.verdict)
            if landing_integration:
                self._escalate_landing_needs_human(task, ws, body.text)
                self._refresh_run(task)
                return
            _set_ws_status(
                self.store,
                ws.id,
                IssueItemStatus.rejected,
                "rejected at review — see the GitHub issue comment",
            )
            self._refresh_run(task)
            return
        branch = issue_branch(ws.issue_number)
        log.info(
            "review task %s ACCEPTED issue #%s — merging %s and closing the issue",
            task.id,
            ws.issue_number,
            branch,
        )
        try:
            self.merge_branch(task.repo, branch, self.config.gh_token, message=f"Resolve #{ws.issue_number} via Hive")
            self.resolve_issue_on_github(
                task.repo,
                ws.issue_number,
                comment=self._issue_resolution_comment(task, body.text, branch),
                token=self.config.gh_token,
            )
        except Exception as exc:
            if _is_merge_conflict(exc):
                project = self.store.get(Project, task.project_id)
                run = self.store.get(IssueRun, task.run_id) if task.run_id else None
                if project:
                    repair = create_landing_integration_task(
                        self.store,
                        project,
                        ws,
                        failure=str(exc),
                        accepted_review=body.text,
                        backend=task.backend,
                        model=task.model,
                        run=run,
                    )
                    if run:
                        refresh_issue_run(self.store, project, run)
                    log.info(
                        "landing issue #%s hit merge conflict; queued integration review task %s",
                        ws.issue_number,
                        repair.id,
                    )
                    return
            log.error("landing issue #%s failed (merge/close): %s", ws.issue_number, exc)
            escalate(
                self.store,
                f"Land issue #{ws.issue_number} failed",
                instructions=(
                    f"The review accepted the fix on `{branch}`, but merging it into the "
                    f"default branch or closing issue #{ws.issue_number} failed:\n\n{exc}\n\n"
                    "Land it manually (the branch is intact)."
                ),
                project_id=task.project_id,
                workspace_id=task.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:land:{task.project_id}:{ws.issue_number}",
                resolution={"check": "workstream_done", "workstream_id": ws.id},
            )
            _set_ws_status(self.store, ws.id, IssueItemStatus.rejected, f"{LANDING_FAILED_PREFIX}: {exc}")
            self._refresh_run(task)
            return
        log.info("issue #%s landed: merged + closed; workstream done", ws.issue_number)
        try:
            self.delete_branch(task.repo, branch, self.config.gh_token)
        except Exception as exc:  # never fail a completed landing over branch cleanup
            log.info("issue #%s landed; leftover branch %s not deleted: %s", ws.issue_number, branch, exc)
        _set_ws_status(self.store, ws.id, IssueItemStatus.done, "")
        project = self.store.get(Project, task.project_id)
        if project:
            sync_directives_for_issue(
                self.store,
                project,
                ws.issue_number,
                DirectiveStatus.done,
                f"landed: issue #{ws.issue_number} merged and closed",
            )
        self._refresh_run(task)

    # -- iteration-plan items (doc-fed pipeline, wiki/iteration-plan.md) -------

    def _handle_plan_task_result(self, task: Task, body: TaskResult, item: PlanItem) -> None:
        project = self.store.get(Project, task.project_id)
        plan = self.store.get(Plan, task.run_id) if task.run_id else None
        if not project or not plan:
            return
        if body.cancelled:
            # No scan exists to resurrect plan work: an operator cancel parks
            # the item so the human's retry is the explicit way forward.
            plans.set_item_status(
                self.store,
                item.id,
                PlanItemStatus.blocked_clarity,
                "task cancelled by the operator — retry the item to continue",
            )
            return
        if task.kind == TaskKind.resolve:
            self._land_plan_resolve(project, plan, task, body, item)
        else:
            self._land_plan_review(project, plan, task, body, item)
        plans.advance_plan(
            self.store,
            project,
            self.store.get(Plan, plan.id) or plan,
            backend=self.config.issue_backend,
            model=self.config.issue_model,
        )
        finished = self.store.get(Plan, plan.id)
        if finished and finished.status == PlanStatus.complete:
            self.supervisor.wake(
                project.id,
                "Iteration plan complete: every item is merged on the default branch. "
                f"The goal was: {plan.goal[:500]}\n"
                "Summarize the outcome and propose the next iteration's goal and plan.",
            )

    def _land_plan_resolve(
        self, project: Project, plan: Plan, task: Task, body: TaskResult, item: PlanItem
    ) -> None:
        if item.status != PlanItemStatus.resolving:
            return
        if body.is_error:
            plans.set_item_status(
                self.store,
                item.id,
                PlanItemStatus.blocked_clarity,
                f"build task errored — fix the cause and retry:\n\n{body.text[-1500:]}",
            )
            return
        if task.verdict != Verdict.accept:
            report = self._agent_report(body.text) or "the agent reported BLOCKED without a reason"
            plans.set_item_status(self.store, item.id, PlanItemStatus.blocked_clarity, report)
            log.info("plan item '%s' blocked at build (task %s)", item.title, task.id)
            return
        plans.set_item_status(self.store, item.id, PlanItemStatus.reviewing, "")
        review = plans.create_review_task(
            self.store, project, plan, item, backend=task.backend, model=task.model
        )
        log.info("queued plan review task %s for item '%s' on %s", review.id, item.title, review.branch)

    def _land_plan_review(
        self, project: Project, plan: Plan, task: Task, body: TaskResult, item: PlanItem
    ) -> None:
        if item.status != PlanItemStatus.reviewing:
            return
        if body.is_error:
            plans.set_item_status(
                self.store,
                item.id,
                PlanItemStatus.rejected,
                f"review task errored — retry the item:\n\n{body.text[-1500:]}",
            )
            return
        if task.verdict != Verdict.accept:
            report = self._agent_report(body.text) or "the review rejected without a report"
            plans.set_item_status(self.store, item.id, PlanItemStatus.rejected, report)
            log.info("plan item '%s' rejected at review (task %s)", item.title, task.id)
            return
        branch = plans.plan_branch(item)
        try:
            # Strict sequencing makes landing conflicts rare (each item branches
            # after the prior merge), so there is no auto-integration chain here:
            # any landing failure parks the item and files the todo.
            self.merge_branch(
                task.repo, branch, self.config.gh_token,
                message=f"Land plan item '{item.title}' via Hive",
            )
        except Exception as exc:
            log.error("landing plan item '%s' failed: %s", item.title, exc)
            plans.set_item_status(
                self.store, item.id, PlanItemStatus.rejected, f"{plans.LANDING_FAILED_PREFIX}: {exc}"
            )
            escalate(
                self.store,
                f"Land plan item '{item.title}' failed",
                instructions=(
                    f"The review accepted the work on `{branch}`, but merging it into the "
                    f"default branch failed:\n\n{exc}\n\n"
                    "Land it manually (the branch is intact), then cancel or retry the item."
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:plan-land:{project.id}:{item.id}",
                resolution={"check": "plan_item_done", "plan_item_id": item.id},
            )
            return
        try:
            self.delete_branch(task.repo, branch, self.config.gh_token)
        except Exception as exc:  # never fail a completed landing over branch cleanup
            log.info("plan item '%s' landed; leftover branch %s not deleted: %s", item.title, branch, exc)
        plans.set_item_status(self.store, item.id, PlanItemStatus.done, "")
        log.info("plan item '%s' landed: merged on the default branch", item.title)

    @staticmethod
    def _is_landing_integration_task(task: Task) -> bool:
        return LANDING_INTEGRATION_PROMPT in task.prompt_versions

    def _escalate_landing_needs_human(self, task: Task, ws: IssueItem, report: str) -> None:
        branch = issue_branch(ws.issue_number)
        _set_ws_status(
            self.store,
            ws.id,
            IssueItemStatus.rejected,
            f"{LANDING_FAILED_PREFIX}: integration needs human input",
        )
        escalate(
            self.store,
            f"Land issue #{ws.issue_number} failed",
            instructions=(
                f"Hive tried to integrate accepted branch `{branch}` with the latest default branch, "
                "but the integration agent reported that resolving it needs human input or a "
                "tradeoff decision.\n\n"
                f"Agent report:\n\n{report[:4000]}"
            ),
            project_id=task.project_id,
            workspace_id=task.workspace_id,
            kind=HumanTaskKind.repair,
            dedup_key=f"repair:land:{task.project_id}:{ws.issue_number}",
            resolution={"check": "workstream_done", "workstream_id": ws.id},
        )

    def _agent_report(self, text: str) -> str:
        lines = [
            line.rstrip()
            for line in text.splitlines()
            if not ISSUE_RESULT_MARKER_RE.match(line)
        ]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines).strip()

    @staticmethod
    def _comment_section(text: str) -> str:
        text = text.strip()
        if len(text) <= ISSUE_COMMENT_SECTION_LIMIT:
            return text
        return text[:ISSUE_COMMENT_SECTION_LIMIT].rstrip() + "\n\n[Truncated by Hive.]"

    def _latest_resolve_report(self, review_task: Task) -> str:
        tasks = [
            t
            for t in self.store.list(
                Task,
                project_id=review_task.project_id,
                workstream_id=review_task.workstream_id,
                kind=TaskKind.resolve,
            )
            if (
                t.issue_number == review_task.issue_number
                and t.verdict == Verdict.accept
                and t.result_text.strip()
            )
        ]
        return self._agent_report(tasks[-1].result_text) if tasks else ""

    def _issue_resolution_comment(self, review_task: Task, review_text: str, branch: str) -> str:
        parts = [f"Resolved by Hive — merged `{branch}` into the default branch."]
        resolve_report = self._latest_resolve_report(review_task)
        review_report = self._agent_report(review_text)
        if resolve_report:
            parts.append(f"### Fix summary\n{self._comment_section(resolve_report)}")
        if review_report:
            parts.append(f"### Review summary\n{self._comment_section(review_report)}")
        return "\n\n".join(parts)

    def _should_advance_after_issue_result(self, task: Task) -> bool:
        if task.kind not in (TaskKind.resolve, TaskKind.review):
            return False
        ws = self.store.get(IssueItem, task.workstream_id)
        if ws is None:
            return True
        if ws.status == IssueItemStatus.rejected and ws.parked_reason.startswith(LANDING_FAILED_PREFIX):
            return False
        return True

    def _advance_after_issue_result(self, task: Task) -> None:
        project = self.store.get(Project, task.project_id)
        if project:
            run = self.store.get(IssueRun, task.run_id) if task.run_id else None
            workstream = self.store.get(ProjectWorkstream, run.workstream_id) if run else None
            advance_issues(
                self.store,
                project,
                workstream=workstream,
                run=run,
                backend=self.config.issue_backend,
                model=self.config.issue_model,
            )

    def _fail_test_episode(self, episode: TestEpisode | None, reason: str) -> None:
        if not episode:
            return

        def mark(saved: TestEpisode) -> None:
            saved.status = TestEpisodeStatus.failed
            saved.finished_at = saved.finished_at or time.time()
            saved.counts = {**saved.counts, "failure": reason[:500]}

        self.store.update(TestEpisode, episode.id, mark)

    def _block_story_on_bad_sweep(
        self,
        project: Project,
        story: Story,
        task: Task,
        episode: TestEpisode | None,
        reason: str,
        output: str,
    ) -> None:
        story.status = StoryStatus.blocked
        story.last_episode_id = task.run_id
        story.last_result_task_id = task.id
        story.updated_at = time.time()
        self.store.put(story)
        escalate(
            self.store,
            f"Repair testing sweep output for {story.key}",
            instructions=(
                "Hive's testing sweep reported findings, but none were actionable enough "
                "to enter the confirmation funnel. The story was blocked instead of filing "
                "a weak or malformed issue.\n\n"
                f"Story: `{story.key}`\n"
                f"Task: `{task.id}`\n"
                f"Reason: {reason}\n\n"
                f"Task output:\n\n```\n{output.strip()[:2000]}\n```"
            ),
            project_id=project.id,
            workspace_id=project.workspace_id,
            kind=HumanTaskKind.env,
            dedup_key=f"env:story:{story.id}",
            resolution={"check": "story_verdict", "story_id": story.id},
        )
        if episode:
            refresh_episode_counts(self.store, project, episode)

    def _block_story_on_sweep_blocked(
        self,
        project: Project,
        story: Story,
        task: Task,
        episode: TestEpisode | None,
        payload: dict,
        output: str,
    ) -> None:
        story.status = StoryStatus.blocked
        story.last_episode_id = task.run_id
        story.last_result_task_id = task.id
        story.updated_at = time.time()
        self.store.put(story)
        summary = str(payload.get("summary") or "").strip()
        evidence = task.artifact_blobs or []
        evidence_lines = "\n".join(f"- `{name}`" for name in evidence) or "(none uploaded)"
        escalate(
            self.store,
            f"Unblock testing sweep for {story.key}",
            instructions=(
                "Hive's testing sweep could not reach a pass/fail verdict. The story "
                "was blocked rather than filing a weak issue.\n\n"
                f"Story: `{story.key}`\n"
                f"Task: `{task.id}`\n"
                f"Reason: {summary or 'sweep reported SWEEP: BLOCKED'}\n\n"
                f"Evidence artifacts:\n{evidence_lines}\n\n"
                f"Task output:\n\n```\n{output.strip()[:2000]}\n```"
            ),
            project_id=project.id,
            workspace_id=project.workspace_id,
            kind=HumanTaskKind.env,
            dedup_key=f"env:story:{story.id}",
            resolution={"check": "story_verdict", "story_id": story.id},
        )
        if episode:
            refresh_episode_counts(self.store, project, episode)

    def _handle_test_refresh_result(self, task: Task, body: TaskResult) -> None:
        project = self.store.get(Project, task.project_id)
        workstream = self.store.get(ProjectWorkstream, task.workstream_id)
        episode = self.store.get(TestEpisode, task.run_id) if task.run_id else None
        if not project or not workstream:
            return
        if body.cancelled:
            if episode:
                def cancel(saved: TestEpisode) -> None:
                    saved.status = TestEpisodeStatus.cancelled
                    saved.finished_at = time.time()

                self.store.update(TestEpisode, episode.id, cancel)
            return
        if body.is_error or not _test_refresh_done(body):
            self._fail_test_episode(episode, "test refresh failed or omitted REFRESH: DONE")
            return
        try:
            spec = SpecRepo(
                project.spec_repo,
                Path(self.config.data_dir or "/tmp/hive-data") / "specs",
                self.config.gh_token,
            )
            spec.sync()
            finalization = finalize_refresh(
                self.store,
                project,
                workstream,
                spec.path,
                episode=episode,
                refresh_result=body.structured_result,
            )
            if finalization.blocked_reason:
                notes = "\n".join(f"- {note}" for note in finalization.report.notes) or "(none)"
                escalate(
                    self.store,
                    f"Repair testing refresh for {project.name}",
                    instructions=(
                        "Hive's test-refresh task finished, but the reconciled acceptance "
                        "backlog is not safe to sweep yet.\n\n"
                        f"Reason: {finalization.blocked_reason}\n"
                        f"Task: `{task.id}`\n\n"
                        f"Reconcile notes:\n{notes}"
                    ),
                    project_id=project.id,
                    workspace_id=project.workspace_id,
                    kind=HumanTaskKind.repair,
                    dedup_key=f"repair:test-refresh:{project.id}",
                )
            else:
                resolve_todo(
                    self.store,
                    project.workspace_id,
                    f"repair:test-refresh:{project.id}",
                    "a later test-refresh finalized cleanly",
                )
        except Exception as exc:
            log.exception("test refresh finalization failed for task %s", task.id)
            self._fail_test_episode(episode, f"{type(exc).__name__}: {exc}")
            escalate(
                self.store,
                f"Repair testing refresh for {project.name}",
                instructions=(
                    "Hive's test-refresh task finished, but the chief could not "
                    "sync/reconcile `acceptance/` afterward.\n\n"
                    f"Task: `{task.id}`\n\nError:\n\n```\n{type(exc).__name__}: {str(exc)[:1500]}\n```"
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:test-refresh:{project.id}",
            )

    def _handle_test_sweep_result(self, task: Task, body: TaskResult) -> None:
        project = self.store.get(Project, task.project_id)
        story = self.store.get(Story, task.work_item_id or task.workstream_id)
        episode = self.store.get(TestEpisode, task.run_id) if task.run_id else None
        if not project or not story:
            return
        if body.cancelled:
            if episode:
                refresh_episode_counts(self.store, project, episode)
            return
        if body.is_error:
            story.status = StoryStatus.blocked
            story.last_episode_id = task.run_id
            story.last_result_task_id = task.id
            story.updated_at = time.time()
            self.store.put(story)
            if episode:
                refresh_episode_counts(self.store, project, episode)
            return
        payload = _test_sweep_payload(body)
        outcome = _test_sweep_outcome(body)
        if outcome == TestSweepOutcome.passed:
            self._mark_story_passing(project, story, task, episode, payload)
        elif outcome == TestSweepOutcome.findings:
            self._handle_sweep_findings(project, story, task, episode, payload, body.text)
        elif outcome == TestSweepOutcome.blocked:
            self._block_story_on_sweep_blocked(project, story, task, episode, payload, body.text)
        else:
            story.status = StoryStatus.blocked
            story.last_episode_id = task.run_id
            story.last_result_task_id = task.id
            story.updated_at = time.time()
            self.store.put(story)
        if episode:
            refresh_episode_counts(self.store, project, episode)

    def _mark_story_passing(
        self,
        project: Project,
        story: Story,
        task: Task,
        episode: TestEpisode | None,
        payload: dict,
    ) -> None:
        # A green story closes the issue of every confirmed finding filed
        # against it — a story can carry several — not just the last one.
        confirmed = [
            f
            for f in self.store.list(
                Finding,
                workspace_id=project.workspace_id,
                project_id=project.id,
                workstream_id=story.workstream_id,
                story_key=story.key,
                status=FindingStatus.confirmed,
            )
            if f.issue_number
        ]
        failed_closes: list[int] = []
        for finding in confirmed:
            try:
                self.close_issue(
                    story.repo or finding.repo,
                    finding.issue_number,
                    self.config.gh_token,
                    f"Hive re-tested story `{story.key}` in episode `{task.run_id}` and it passed.",
                )
            except Exception as exc:
                log.warning("could not close green story issue #%s: %s", finding.issue_number, exc)
                failed_closes.append(finding.issue_number)
                continue
            finding.status = FindingStatus.resolved
            finding.updated_at = time.time()
            self.store.put(finding)
        if failed_closes:
            numbers = ", ".join(f"#{n}" for n in failed_closes)
            escalate(
                self.store,
                f"Close testing issue(s) {numbers} failed",
                instructions=(
                    f"Story `{story.key}` passed in task `{task.id}`, but Hive could not "
                    f"close {numbers} automatically."
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:close-issues:{story.id}",
            )
        else:
            story.open_issue_number = 0
            story.open_issue_url = ""
        story.status = StoryStatus.passing
        story.last_tested_baseline = story.spec_baseline
        story.last_fidelity = (
            StoryFidelity.docker if payload.get("fidelity") == "docker" else StoryFidelity.local
        )
        story.last_episode_id = task.run_id
        story.last_result_task_id = task.id
        story.last_tested_at = task.finished_at or time.time()
        story.updated_at = time.time()
        self.store.put(story)

    def _handle_sweep_findings(
        self,
        project: Project,
        story: Story,
        task: Task,
        episode: TestEpisode | None,
        payload: dict,
        output: str,
    ) -> None:
        raw_findings = payload.get("findings") if isinstance(payload, dict) else None
        findings = persist_sweep_findings(
            self.store,
            project,
            story,
            task,
            episode
            or TestEpisode(project_id=project.id, workstream_id=story.workstream_id, repo=story.repo),
            payload,
        )
        if not findings:
            if not isinstance(raw_findings, list) or not raw_findings:
                reason = "missing or malformed findings JSON"
            else:
                problems = [
                    finding_quality_problem(item)
                    for item in raw_findings
                    if isinstance(item, dict)
                ]
                reason = ", ".join(dict.fromkeys(p for p in problems if p)) or "no actionable findings"
            self._block_story_on_bad_sweep(project, story, task, episode, reason, output)
            return
        story.status = StoryStatus.failing
        # A failing sweep is still a test against the current baseline — record
        # it, or the next backlog reconcile downgrades the failure to `stale`.
        story.last_tested_baseline = story.spec_baseline
        story.last_episode_id = task.run_id
        story.last_result_task_id = task.id
        story.last_tested_at = task.finished_at or time.time()
        story.last_fidelity = (
            StoryFidelity.docker if payload.get("fidelity") == "docker" else StoryFidelity.local
        )
        story.updated_at = time.time()
        self.store.put(story)
        if episode:
            for finding in findings:
                queue_confirm_task(self.store, project, story, finding, episode)

    def _confirm_test_finding(
        self,
        project: Project,
        story: Story,
        finding: Finding,
        episode: TestEpisode | None,
    ) -> None:
        try:
            number, url = self.file_or_update_finding_issue(
                story.repo or finding.repo,
                finding,
                story,
                self.config.gh_token,
            )
        except Exception as exc:
            log.exception("filing testing issue failed for finding %s", finding.id)
            self._fail_test_episode(episode, f"file GitHub issue failed: {type(exc).__name__}: {exc}")
            escalate(
                self.store,
                f"File testing issue failed for {story.key}",
                instructions=(
                    f"Hive confirmed a testing finding but could not file/update the GitHub issue.\n\n"
                    f"Story: `{story.key}`\nFinding: `{finding.summary}`\n\n"
                    f"Error:\n\n```\n{type(exc).__name__}: {str(exc)[:1500]}\n```"
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:file-issue:{story.id}",
            )
            return
        finding.issue_number = number
        finding.issue_url = url
        finding.status = FindingStatus.confirmed
        finding.updated_at = time.time()
        self.store.put(finding)
        story.status = StoryStatus.failing
        story.open_issue_number = number
        story.open_issue_url = url
        story.updated_at = time.time()
        self.store.put(story)

    def _block_test_confirmation(
        self,
        project: Project,
        story: Story,
        finding: Finding,
        task: Task,
        episode: TestEpisode | None,
        output: str,
    ) -> None:
        finding.status = FindingStatus.blocked
        finding.detail = (finding.detail + "\n\nConfirmation task blocked:\n" + output).strip()
        finding.updated_at = time.time()
        self.store.put(finding)
        story.status = StoryStatus.blocked
        story.last_episode_id = task.run_id
        story.last_result_task_id = task.id
        story.updated_at = time.time()
        self.store.put(story)
        escalate(
            self.store,
            f"Unblock testing confirmation for {story.key}",
            instructions=(
                "Hive found a suspected testing issue, but the independent confirmation "
                "task failed before it could decide whether the finding is real. The "
                "finding was blocked, not rejected.\n\n"
                f"Story: `{story.key}`\n"
                f"Finding: {finding.summary}\n"
                f"Task: `{task.id}`\n\n"
                f"Task output:\n\n```\n{output.strip()[:2000]}\n```"
            ),
            project_id=project.id,
            workspace_id=project.workspace_id,
            kind=HumanTaskKind.env,
            dedup_key=f"env:finding:{finding.id}",
            resolution={"check": "finding_decided", "finding_id": finding.id},
        )
        if episode:
            refresh_episode_counts(self.store, project, episode)

    def _handle_test_confirm_result(self, task: Task, body: TaskResult) -> None:
        project = self.store.get(Project, task.project_id)
        finding = self.store.get(Finding, task.workstream_id)
        story = self.store.get(Story, task.work_item_id)
        episode = self.store.get(TestEpisode, task.run_id) if task.run_id else None
        if not project or not finding or not story:
            return
        if body.cancelled:
            if episode:
                refresh_episode_counts(self.store, project, episode)
            return
        if body.is_error:
            self._block_test_confirmation(project, story, finding, task, episode, body.text)
            return
        if task.kind == TaskKind.test_reproduce:
            outcome = _test_repro_outcome(body)
            if outcome == TestReproOutcome.confirmed:
                self._confirm_test_finding(project, story, finding, episode)
            else:
                finding.status = FindingStatus.rejected
                finding.updated_at = time.time()
                self.store.put(finding)
        elif task.kind == TaskKind.test_judge:
            outcome = _test_ux_outcome(body)
            if outcome == TestUxOutcome.improvable:
                self._confirm_test_finding(project, story, finding, episode)
            elif outcome == TestUxOutcome.constrained:
                finding.status = FindingStatus.constrained
                finding.updated_at = time.time()
                self.store.put(finding)
                note = body.text.strip()
                story.known_limitations = list(dict.fromkeys([*story.known_limitations, note[:1000]]))
                story.updated_at = time.time()
                self.store.put(story)
            else:
                finding.status = FindingStatus.rejected
                finding.updated_at = time.time()
                self.store.put(finding)
        if episode:
            refresh_episode_counts(self.store, project, episode)

    def _handle_test_task_result(self, task: Task, body: TaskResult) -> None:
        if task.kind == TaskKind.test_refresh:
            self._handle_test_refresh_result(task, body)
        elif task.kind == TaskKind.test_sweep:
            self._handle_test_sweep_result(task, body)
        elif task.kind in (TaskKind.test_reproduce, TaskKind.test_judge):
            self._handle_test_confirm_result(task, body)
        elif task.kind == TaskKind.testability_draft:
            self._handle_testability_draft_result(task, body)
        elif task.kind == TaskKind.testability_probe:
            self._handle_testability_probe_result(task, body)

    def _handle_testability_draft_result(self, task: Task, body: TaskResult) -> None:
        project = self.store.get(Project, task.project_id)
        workstream = self.store.get(ProjectWorkstream, task.workstream_id)
        if not project or not workstream or body.cancelled:
            return
        if body.is_error or task.verdict != Verdict.accept:
            escalate(
                self.store,
                f"Repair testability draft for {project.name}",
                instructions=(
                    "Hive's testability-draft task did not produce a usable contract.\n\n"
                    f"Task: `{task.id}`\n\nAgent report (tail):\n\n```\n{body.text[-1500:]}\n```"
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:testability:{workstream.id}",
            )
            return
        try:
            spec = SpecRepo(
                project.spec_repo,
                Path(self.config.data_dir or "/tmp/hive-data") / "specs",
                self.config.gh_token,
            )
            spec.sync()
            summary = DraftResultSummary.from_payload(body.structured_result)
            contract = reconcile_contract(self.store, project, workstream, spec.path)
            questions = create_decision_questions(self.store, project, workstream, summary.decisions)
        except Exception as exc:
            log.exception("testability draft finalization failed for task %s", task.id)
            escalate(
                self.store,
                f"Repair testability draft for {project.name}",
                instructions=(
                    "Hive's testability-draft task finished, but the chief could not "
                    "sync/reconcile `testability.md` afterward.\n\n"
                    f"Task: `{task.id}`\n\nError:\n\n```\n{type(exc).__name__}: {str(exc)[:1500]}\n```"
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:testability:{workstream.id}",
            )
            return
        if not contract.content:
            escalate(
                self.store,
                f"Repair testability draft for {project.name}",
                instructions=(
                    "The testability-draft task reported DONE, but the spec home has no "
                    f"`testability.md` after syncing.\n\nTask: `{task.id}`"
                ),
                project_id=project.id,
                workspace_id=project.workspace_id,
                kind=HumanTaskKind.repair,
                dedup_key=f"repair:testability:{workstream.id}",
            )
            return
        resolve_todo(
            self.store,
            project.workspace_id,
            f"repair:testability:{workstream.id}",
            "a later testability draft finalized cleanly",
        )
        # Proving the contract is Hive's job, not a user step: chain the probe.
        probe = queue_probe_task(
            self.store,
            project,
            workstream,
            contract,
            backend=self.config.test_confirm_backend,
            model=self.config.test_confirm_model,
        )
        self.supervisor.wake(
            task.project_id,
            f"Testability contract drafted for {workstream.repo}; probe task {probe.id} queued"
            + (f"; {len(questions)} decision question(s) filed." if questions else "."),
        )

    def _handle_testability_probe_result(self, task: Task, body: TaskResult) -> None:
        project = self.store.get(Project, task.project_id)
        workstream = self.store.get(ProjectWorkstream, task.workstream_id)
        if not project or not workstream or body.cancelled:
            return
        contract = self.store.get(TestabilityContract, task.work_item_id) or get_contract(
            self.store, project, workstream
        )
        if not contract:
            return
        payload = body.structured_result or test_payload(body.text)
        ok = not body.is_error and task.verdict == Verdict.accept
        problems = [str(p).strip() for p in payload.get("problems") or [] if str(p).strip()]
        if not ok and not problems:
            problems = [body.text.strip()[-500:] or "probe failed without a report"]
        record_probe_result(
            self.store,
            contract,
            ok=ok,
            fidelity=str(payload.get("fidelity") or "local"),
            problems=problems,
            task_id=task.id,
        )
        if ok:
            resolve_todo(
                self.store,
                project.workspace_id,
                f"env:testability:{workstream.id}",
                "a later probe proved the testability contract",
            )
            return
        listed = "\n".join(f"- {p}" for p in problems[:6])
        escalate(
            self.store,
            f"Testability contract failed its probe for {project.name}",
            instructions=(
                f"Hive tried to stand `{workstream.repo}` up exactly as `testability.md` "
                "says and failed. If this is an environment gap (daemon down, missing "
                "tool), fix it on the runner; if the contract is wrong, Hive will "
                "repair it on the next draft.\n\n"
                f"Problems:\n{listed}\n\nTask: `{task.id}`"
            ),
            project_id=project.id,
            workspace_id=project.workspace_id,
            kind=HumanTaskKind.env,
            dedup_key=f"env:testability:{workstream.id}",
        )

    def _wake_default(self, task: Task, body: TaskResult) -> None:
        outcome = "cancelled" if body.cancelled else ("failed" if body.is_error else "finished")
        verdict_note = (
            f" verdict={task.verdict}"
            if task.kind in (TaskKind.resolve, TaskKind.review) and not body.cancelled
            else ""
        )
        self.supervisor.wake(
            task.project_id,
            f"{task.kind} task {task.id} (ws {task.workstream_id}, repo {task.repo}) "
            f"{outcome}{verdict_note}.\nResult:\n{body.text[:6000]}",
        )
