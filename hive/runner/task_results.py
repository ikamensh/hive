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

from hive.runner.agent_results import (
    test_repro_outcome as structured_test_repro_outcome,
    test_sweep_outcome as structured_test_sweep_outcome,
    test_ux_outcome as structured_test_ux_outcome,
    verdict_from_structured,
)
from hive.runner.backends import REGISTRY
from hive.config.settings import Config
from hive.control.escalation import escalate
from hive.workstreams._issues import (
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
)
from hive.models import (
    AgentConversation,
    ConversationStatus,
    Finding,
    FindingStatus,
    HumanTask,
    HumanTaskStatus,
    IssueRun,
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
    TestEpisode,
    TestEpisodeStatus,
    TestReproOutcome,
    TestSweepOutcome,
    TestUxOutcome,
    Verdict,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
    parse_resolve,
    parse_review,
    parse_test_refresh,
    parse_test_repro,
    parse_test_sweep,
    parse_test_ux,
    parse_verdict,
)
from hive.integrations._specrepo import SpecRepo
from hive.workstreams._testing import (
    close_story_issue as default_close_story_issue,
    file_or_update_finding_issue as default_file_or_update_finding_issue,
    finding_quality_problem,
    finalize_refresh,
    persist_sweep_findings,
    queue_confirm_task,
    refresh_episode_counts,
    result_payload as test_payload,
)

log = logging.getLogger("hive.runner.task_results")

RATE_LIMIT_COOLDOWN_S = 3600.0
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


def _set_ws_status(store, ws_id: str, status: WorkstreamStatus, reason: str) -> Workstream | None:
    def mutate(ws: Workstream) -> None:
        ws.status = status
        ws.parked_reason = reason

    return store.update(Workstream, ws_id, mutate)


def cancel_issue_work(store, task: Task) -> None:
    if task.kind in (TaskKind.resolve, TaskKind.review) and task.workstream_id:
        _set_ws_status(
            store,
            task.workstream_id,
            WorkstreamStatus.queued,
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
    for ws in store.list(Workstream, project_id=project.id):
        if (
            ws.source == WorkstreamSource.issue
            and ws.issue_number == issue_number
            and ws.status == WorkstreamStatus.rejected
            and ws.parked_reason.startswith(LANDING_FAILED_PREFIX)
        ):
            _set_ws_status(store, ws.id, WorkstreamStatus.done, "")
            log.info("human task %s confirmed issue #%s is closed; marked workstream done", task.id, issue_number)
            return


def complete_resource_login_todos(store, resource: Resource) -> None:
    runner = store.get(Runner, resource.runner_id)
    runner_name = runner.name if runner else resource.runner_id
    title = f"Fix {resource.backend} login on {runner_name}"
    for task in store.list(HumanTask, workspace_id=resource.workspace_id):
        if (
            task.status == HumanTaskStatus.open
            and task.project_id == ""
            and task.title == title
        ):
            task.status = HumanTaskStatus.done
            task.done_at = time.time()
            store.put(task)


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
        close_story_issue_func: Callable[..., None] = default_close_story_issue,
    ) -> None:
        self.store = store
        self.supervisor = supervisor
        self.config = config
        self.merge_branch = merge_branch_func
        self.resolve_issue_on_github = resolve_issue_func
        self.delete_branch = delete_branch_func
        self.file_or_update_finding_issue = file_finding_issue_func
        self.close_story_issue = close_story_issue_func

    def handle(self, task_id: str, body: TaskResult, workspace_id: str) -> dict:
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

        if task.kind == TaskKind.resolve and not body.cancelled:
            self._land_resolve(task, body)
        elif task.kind == TaskKind.review and not body.cancelled:
            self._land_review(task, body)
        elif task.kind in (TaskKind.resolve, TaskKind.review) and body.cancelled:
            cancel_issue_work(self.store, task)

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
        if task.kind == TaskKind.verify and not body.cancelled:
            task.verdict = _structured_or_legacy_verdict(
                task.kind,
                body,
                parse_verdict(body.text),
            )
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

    def _account_resources(
        self,
        workspace_id: str,
        task: Task,
        body: TaskResult,
    ) -> list[Resource]:
        probe_resources: list[Resource] = []

        def account(resource: Resource) -> None:
            resource.total_tasks += 1
            resource.total_cost_usd += body.cost_usd
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
                # otherwise let it silently look "usable" again.
                resource.usability_status = ResourceUsability.failed
                resource.clear_exhaustion()
            if body.resource_exhausted:
                resource.mark_exhausted(
                    until=time.time() + RATE_LIMIT_COOLDOWN_S,
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
            if updated and task.kind == TaskKind.probe and updated.last_probe_task_id == task.id:
                probe_resources.append(updated)
        return probe_resources

    def _handle_probe_result(
        self,
        task: Task,
        body: TaskResult,
        probe_resources: list[Resource],
        workspace_id: str,
    ) -> None:
        if not body.cancelled and not body.is_error and not body.resource_exhausted:
            for resource in probe_resources:
                if resource.enabled:
                    complete_resource_login_todos(self.store, resource)
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

    def _escalate_intake_failure(self, task: Task, body: TaskResult, project: Project) -> None:
        """Intake hit a wall. File an operator todo so the project isn't a silent
        dead-end. An auth/policy block reuses the same "Fix <backend> login"
        title as a failed probe, so a later successful re-probe auto-closes it
        (see `complete_resource_login_todos`); other failures get a retry todo."""
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
        else:
            title = f"Intake scout failed for {project.name}"
            instructions = (
                f"The `{task.backend}` intake scout for **{project.name}** failed:\n\n"
                f"```\n{detail}\n```\n\n"
                "Retry intake from the project setup (optionally with a different trusted scout)."
            )
        escalate(
            self.store,
            title,
            instructions=instructions,
            project_id="" if body.auth_blocked else project.id,
            workspace_id=project.workspace_id,
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

        def transition(ws: Workstream) -> None:
            if ws.status != WorkstreamStatus.resolving:
                return
            if task.verdict == Verdict.accept:
                ws.status = WorkstreamStatus.reviewing
                ws.parked_reason = ""
            else:
                ws.status = WorkstreamStatus.blocked_clarity
                ws.parked_reason = "blocked at clarify step — see the GitHub issue comment"

        ws = self.store.update(Workstream, task.workstream_id, transition)
        if ws is None:
            return
        log.info(
            "resolve task %s (issue #%s) verdict=%s → workstream %s",
            task.id,
            task.issue_number,
            task.verdict,
            ws.status,
        )
        if ws.status == WorkstreamStatus.reviewing:
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

    def _land_review(self, task: Task, body: TaskResult) -> None:
        ws = self.store.get(Workstream, task.workstream_id)
        if ws is None or ws.status != WorkstreamStatus.reviewing:
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
            _set_ws_status(self.store, ws.id, WorkstreamStatus.rejected, reason)
            project = self.store.get(Project, task.project_id)
            run = self.store.get(IssueRun, task.run_id) if task.run_id else None
            if project and run:
                refresh_issue_run(self.store, project, run)
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
                project = self.store.get(Project, task.project_id)
                run = self.store.get(IssueRun, task.run_id) if task.run_id else None
                if project and run:
                    refresh_issue_run(self.store, project, run)
                return
            _set_ws_status(
                self.store,
                ws.id,
                WorkstreamStatus.rejected,
                "rejected at review — see the GitHub issue comment",
            )
            project = self.store.get(Project, task.project_id)
            run = self.store.get(IssueRun, task.run_id) if task.run_id else None
            if project and run:
                refresh_issue_run(self.store, project, run)
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
            )
            _set_ws_status(self.store, ws.id, WorkstreamStatus.rejected, f"{LANDING_FAILED_PREFIX}: {exc}")
            project = self.store.get(Project, task.project_id)
            run = self.store.get(IssueRun, task.run_id) if task.run_id else None
            if project and run:
                refresh_issue_run(self.store, project, run)
            return
        log.info("issue #%s landed: merged + closed; workstream done", ws.issue_number)
        try:
            self.delete_branch(task.repo, branch, self.config.gh_token)
        except Exception as exc:  # never fail a completed landing over branch cleanup
            log.info("issue #%s landed; leftover branch %s not deleted: %s", ws.issue_number, branch, exc)
        _set_ws_status(self.store, ws.id, WorkstreamStatus.done, "")
        project = self.store.get(Project, task.project_id)
        run = self.store.get(IssueRun, task.run_id) if task.run_id else None
        if project and run:
            refresh_issue_run(self.store, project, run)

    @staticmethod
    def _is_landing_integration_task(task: Task) -> bool:
        return LANDING_INTEGRATION_PROMPT in task.prompt_versions

    def _escalate_landing_needs_human(self, task: Task, ws: Workstream, report: str) -> None:
        branch = issue_branch(ws.issue_number)
        _set_ws_status(
            self.store,
            ws.id,
            WorkstreamStatus.rejected,
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
        ws = self.store.get(Workstream, task.workstream_id)
        if ws is None:
            return True
        if ws.status == WorkstreamStatus.rejected and ws.parked_reason.startswith(LANDING_FAILED_PREFIX):
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
        closed = False
        if story.open_issue_number:
            try:
                self.close_story_issue(
                    story.repo,
                    story,
                    self.config.gh_token,
                    f"Hive re-tested story `{story.key}` in episode `{task.run_id}` and it passed.",
                )
                closed = True
            except Exception as exc:
                log.warning("could not close green story issue #%s: %s", story.open_issue_number, exc)
                escalate(
                    self.store,
                    f"Close testing issue #{story.open_issue_number} failed",
                    instructions=(
                        f"Story `{story.key}` passed in task `{task.id}`, but Hive could not "
                        f"close issue #{story.open_issue_number} automatically.\n\n{exc}"
                    ),
                    project_id=project.id,
                    workspace_id=project.workspace_id,
                )
        story.status = StoryStatus.passing
        story.last_tested_baseline = story.spec_baseline
        story.last_fidelity = (
            StoryFidelity.docker if payload.get("fidelity") == "docker" else StoryFidelity.local
        )
        story.last_episode_id = task.run_id
        story.last_result_task_id = task.id
        story.last_tested_at = task.finished_at or time.time()
        if closed:
            story.open_issue_number = 0
            story.open_issue_url = ""
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

    def _wake_default(self, task: Task, body: TaskResult) -> None:
        outcome = "cancelled" if body.cancelled else ("failed" if body.is_error else "finished")
        verdict_note = (
            f" verdict={task.verdict}"
            if task.kind in (TaskKind.verify, TaskKind.resolve, TaskKind.review)
            and not body.cancelled
            else ""
        )
        self.supervisor.wake(
            task.project_id,
            f"{task.kind} task {task.id} (ws {task.workstream_id}, repo {task.repo}) "
            f"{outcome}{verdict_note}.\nResult:\n{body.text[:6000]}",
        )
