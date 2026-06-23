"""FastAPI chief: web API + runner protocol + app wiring.

Build with `create_app()` for production (env config) or pass explicit pieces
in tests. Web endpoints are unauthenticated (the service sits behind
Tailscale); runner endpoints require the shared runner token.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from hive.integrations.auth import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    AuthContext,
    AuthManager,
    ensure_machine,
)
from hive.runner.backends import probe_instructions
from hive.config.settings import Config
from hive.control.escalation import escalate
from hive.integrations.github_repos import all_repos as list_github_repos
from hive.integrations.github_repos import create_repo as create_github_repo
from hive.integrations.specrepo import REQUIRED_INTAKE_FILES, SpecRepo, SpecStatus, spec_status_dir
from hive.workstreams.issues import (
    advance_issues,
    attachment_key,
    download_issue_attachments,
    ensure_issue_workstream,
    fetch_open_issues_full,
    issue_is_closed,
    merge_branch,
    project_workstreams,
    reconcile,
    refresh_issue_run,
    RESOLVE_BACKEND,
    resolve_issue_on_github,
)
from hive.workstreams.preflight import (
    checks_payload,
    codex_runner_usable,
    create_preflight_task,
    preflight_checks,
)
from hive.workstreams.testing import (
    artifact_key,
    close_story_issue,
    ensure_testing_workstream,
    file_or_update_finding_issue,
    queue_refresh_task,
    reconcile_story_backlog,
    safe_artifact_name,
    start_episode,
)
from hive.models import (
    AgentConversation,
    Autonomy,
    Checkout,
    ConversationStatus,
    Directive,
    DirectiveStatus,
    Feedback,
    Finding,
    GuessPropensity,
    HumanTask,
    HumanTaskStatus,
    IssueRun,
    IssueRunScope,
    IssueRunStatus,
    LicensingMode,
    Machine,
    Mode,
    Project,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    ProjectWorkstreamStatus,
    ProjectState,
    Question,
    QuestionStatus,
    Resource,
    ResourceUsability,
    Runner,
    Story,
    Subscription,
    Task,
    TaskKind,
    TaskStatus,
    TestEpisode,
    TestEpisodeScope,
    TestEpisodeStatus,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)
from hive.integrations.specrepo import SpecRepo
from hive.persistence.storage import storage_info
from hive.control.capacity import (
    group_machines,
    machine_cards,
    resource_available,
    subscription_candidates,
)
from hive.control.overview import build_overview
from hive.control.supervisor import Supervisor
from hive.runner.task_results import (
    TaskResult,
    TaskResultProcessor,
    cancel_issue_work,
    complete_resource_login_todos,
    sync_landing_failure_human_task,
)

log = logging.getLogger("hive.api")

RUNNER_POLL_WAIT_S = 5.0
RUNNER_POLL_SLEEP_S = 1.0
# Probe tasks carry this sentinel instead of a real repo: the runner builds its
# own local probe repo (works for remote runners, no shared filesystem).
PROBE_REPO = "probe:local"


def _iso_utc(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).isoformat()


def canonical_repo(url: str) -> str:
    """Reduce a repo URL to a comparable `owner/repo` (or host path) key, so a
    runner's `git@github.com:o/r.git` origin and the stored
    `https://github.com/o/r` match. Lenient: never raises, lower-cases."""
    u = (url or "").strip()
    for prefix in (
        "git@github.com:",
        "ssh://git@github.com/",
        "https://github.com/",
        "http://github.com/",
        "git://github.com/",
    ):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    return u.removesuffix(".git").strip("/").lower()


class ProjectCreate(BaseModel):
    name: str


class ProjectStart(BaseModel):
    mission: str = ""
    iteration_goal: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    archived: bool | None = None
    spec_repo: str | None = None
    mode: Mode | None = None
    autonomy: Autonomy | None = None
    guess_propensity: GuessPropensity | None = None
    prod_deploys: bool | None = None
    paused: bool | None = None
    daily_budget_usd: float | None = None
    member_repos: list[str] | None = None
    new_iteration_note: str | None = None  # set when starting the next iteration


class IntakeMessage(BaseModel):
    message: str = ""
    action: str = "message"  # message | proceed | approve


class IntakeStart(BaseModel):
    backend: str = ""  # optional: pin the trusted scout (else best available is chosen)


class ProjectRepoCreate(BaseModel):
    name: str = ""
    private: bool = True


class ProjectWorkstreamCreate(BaseModel):
    kind: ProjectWorkstreamKind = ProjectWorkstreamKind.github_issues
    repo: str = ""


class ProjectWorkstreamPatch(BaseModel):
    title: str | None = None
    enabled: bool | None = None
    config: dict | None = None


class IssueRunCreate(BaseModel):
    scope: IssueRunScope = IssueRunScope.all_open_now
    issue_numbers: list[int] = []
    backend: str = ""
    model: str = ""


class TestRefreshCreate(BaseModel):
    backend: str = ""
    model: str = ""


class TestEpisodeCreate(BaseModel):
    scope: TestEpisodeScope = TestEpisodeScope.priority
    story_keys: list[str] = []
    max_stories: int = 0
    refresh_backend: str = ""
    refresh_model: str = ""
    sweep_backend: str = ""
    sweep_model: str = ""
    confirm_backend: str = ""
    confirm_model: str = ""


class AnswerBody(BaseModel):
    answer: str


class FeedbackBody(BaseModel):
    project_id: str
    target_id: str
    verdict: str
    comment: str = ""


class BackendDiscoveryInput(BaseModel):
    name: str
    installed: bool = True
    status: str = "unknown"
    path: str = ""
    version: str = ""
    message: str = ""


class CheckoutReport(BaseModel):
    """Git facts the runner observed for one repo checkout it holds."""

    repo: str  # git URL
    exists: bool = True
    head_sha: str = ""
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    dirty: bool = False


class DirectiveCreate(BaseModel):
    text: str


class RunnerRegister(BaseModel):
    name: str
    backends: list[str]
    machine_id: str = ""
    machine_name: str = ""
    machine_type: str = ""
    machine_os: str = ""
    machine_arch: str = ""
    machine_kind: str = ""
    boot: bool = False  # true on daemon startup (vs periodic heartbeat)
    discoveries: list[BackendDiscoveryInput] = []
    capabilities: list[str] = []
    auto_probe: bool = False
    checkouts: list[CheckoutReport] = []  # git facts per repo this runner has checked out


class ResourcePatch(BaseModel):
    enabled: bool | None = None
    disabled_reason: str = ""


class LocalRunnerPatch(BaseModel):
    autostart: bool


def create_app(store, supervisor: Supervisor, config: Config, blobs=None, local_runner=None) -> FastAPI:
    app = FastAPI(title="hive")
    auth = AuthManager(store, config)
    auth.validate_config()

    def current(request: Request) -> AuthContext:
        return auth.require(request)

    def runner_auth(
        x_hive_token: str = Header(default=""),
        x_hive_workspace: str = Header(default=""),
    ) -> str:
        if x_hive_token != config.runner_token:
            raise HTTPException(401, "bad runner token")
        workspace_id = x_hive_workspace or config.workspace_id
        if workspace_id != config.workspace_id:
            raise HTTPException(403, "runner token is not valid for this workspace")
        return workspace_id

    def require_project(project_id: str, ctx: AuthContext) -> Project:
        project = store.get(Project, project_id)
        if not project or project.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        return project

    def require_project_workstream(
        project: Project,
        workstream_id: str,
        ctx: AuthContext,
    ) -> ProjectWorkstream:
        workstream = store.get(ProjectWorkstream, workstream_id)
        if (
            not workstream
            or workstream.workspace_id != ctx.workspace_id
            or workstream.project_id != project.id
        ):
            raise HTTPException(404)
        return workstream

    def require_enabled_workstream(workstream: ProjectWorkstream) -> None:
        if not workstream.enabled or workstream.status == ProjectWorkstreamStatus.disabled:
            raise HTTPException(409, "workstream is disabled")

    def issue_preflight_checks(project: Project, repo: str):
        try:
            return preflight_checks(store, config, project, repo=repo)
        except TypeError:
            return preflight_checks(store, config, project)

    def sync_issue_workstream(project: Project, workstream: ProjectWorkstream) -> tuple[list[str], int, int, int]:
        if workstream.kind != ProjectWorkstreamKind.github_issues:
            raise HTTPException(400, "workstream does not read GitHub issues")
        require_enabled_workstream(workstream)
        hard_failed = [
            c
            for c in issue_preflight_checks(project, workstream.repo)
            if c.hard and not c.ok
        ]
        if hard_failed:
            raise HTTPException(
                409,
                {
                    "error": "preflight failed; fix these before running issue solving",
                    "checks": checks_payload(hard_failed),
                },
            )
        issues = fetch_open_issues_full(workstream.repo, config.gh_token)
        notes = reconcile(store, project, issues, workstream=workstream)
        downloaded = failed = 0
        if blobs is not None:
            downloaded, failed = download_issue_attachments(
                store,
                blobs,
                project,
                config.gh_token,
                workstream=workstream,
            )
        return notes, len(issues), downloaded, failed

    def start_issue_run(
        project: Project,
        workstream: ProjectWorkstream,
        body: IssueRunCreate,
    ) -> tuple[IssueRun, list[str], int, int, int, int]:
        require_enabled_workstream(workstream)
        notes, open_count, downloaded, failed = sync_issue_workstream(project, workstream)
        open_items = [
            w
            for w in store.list(Workstream, project_id=project.id)
            if w.source == WorkstreamSource.issue
            and w.workstream_id == workstream.id
            and w.issue_number
        ]
        open_numbers = {
            w.issue_number
            for w in open_items
            if w.status not in (WorkstreamStatus.done, WorkstreamStatus.cancelled)
        }
        if body.scope == IssueRunScope.selected:
            selected = [n for n in dict.fromkeys(body.issue_numbers) if n in open_numbers]
            if not selected:
                raise HTTPException(400, "select at least one open issue")
            issue_numbers = selected
        else:
            issue_numbers = sorted(open_numbers)
        status = IssueRunStatus.done if body.scope == IssueRunScope.scan_only else IssueRunStatus.queued
        run = store.put(
            IssueRun(
                workspace_id=project.workspace_id,
                project_id=project.id,
                workstream_id=workstream.id,
                repo=workstream.repo,
                scope=body.scope,
                issue_numbers=issue_numbers,
                status=status,
                counts={
                    "open_issues": open_count,
                    "attachments_downloaded": downloaded,
                    "attachments_failed": failed,
                },
                started_at=time.time() if body.scope != IssueRunScope.scan_only else 0,
                finished_at=time.time() if body.scope == IssueRunScope.scan_only else 0,
            )
        )
        queued = 0
        if body.scope != IssueRunScope.scan_only:
            queued = advance_issues(
                store,
                project,
                workstream=workstream,
                run=run,
                backend=body.backend or config.issue_backend,
                model=body.model or config.issue_model,
            )
            run = store.get(IssueRun, run.id) or run
        else:
            run = refresh_issue_run(store, project, run)
        return run, notes, open_count, queued, downloaded, failed

    def require_testing_workstream(workstream: ProjectWorkstream) -> None:
        if workstream.kind != ProjectWorkstreamKind.testing:
            raise HTTPException(400, "workstream is not a testing workstream")
        require_enabled_workstream(workstream)

    def sync_testing_backlog(project: Project, workstream: ProjectWorkstream) -> None:
        """Read current acceptance files before queueing an agent refresh.

        This makes a missing backlog an explicit bootstrap case instead of
        conflating it with an already-authored spec home that simply has not been
        mirrored into Store rows yet.
        """
        try:
            spec = SpecRepo(
                project.spec_repo,
                Path(config.data_dir or "/tmp/hive-data") / "specs",
                config.gh_token,
            )
            spec.sync()
            reconcile_story_backlog(store, project, workstream, spec.path)
        except Exception as exc:
            log.exception("testing backlog sync failed for project %s", project.id)
            raise HTTPException(400, f"could not read acceptance stories from spec repo: {exc}") from exc

    def can_write_spec_repo(project: Project) -> bool:
        """Avoid slow surprise network attempts in throwaway/local runs.

        Production has HIVE_GH_TOKEN; tests and local smoke runs often use a
        filesystem path. Other remotes can still be handled by the orchestrator
        via commit_to_spec, but the chief only auto-writes when it has
        an obvious write path.
        """
        url = project.spec_repo
        return bool(config.gh_token.strip()) or url.startswith("file://") or Path(url).exists()

    TRUSTED_SCOUTS = (("codex", "gpt-5.5"), ("claude", "opus"))

    def trusted_intake_capacity(
        workspace_id: str, prefer_backend: str = ""
    ) -> tuple[str, str, str]:
        """Return (backend, model, runner_id) for an available trusted intake
        scout. Intake is high leverage, so only trusted backends qualify.

        `prefer_backend` pins the choice when that backend is available (the user
        explicitly picked it on retry); otherwise the first available backend in
        preference order is used, so a project is never stuck because the default
        scout is blocked while another trusted one is ready.
        """
        online = {r.id: r for r in store.list(Runner, workspace_id=workspace_id) if r.online()}
        ordered = sorted(
            TRUSTED_SCOUTS, key=lambda bm: (bm[0] != prefer_backend, TRUSTED_SCOUTS.index(bm))
        )
        if prefer_backend and prefer_backend not in dict(TRUSTED_SCOUTS):
            raise HTTPException(
                400,
                f"unknown trusted scout {prefer_backend!r}; choose one of "
                f"{', '.join(b for b, _ in TRUSTED_SCOUTS)}",
            )
        for backend, model in ordered:
            for resource in store.list(Resource, workspace_id=workspace_id, backend=backend):
                runner = online.get(resource.runner_id)
                if runner and backend in runner.backends and resource.available():
                    return backend, model, runner.id
        raise HTTPException(
            409,
            "intake requires a usable trusted scout backend (codex gpt-5.5 or claude opus); "
            "probe or fix a trusted scout, then retry",
        )

    def spec_status(project: Project) -> SpecStatus:
        if not project.spec_repo.strip():
            return SpecStatus((), (), (), False, "spec_repo is not set")
        try:
            local = Path(project.spec_repo)
            is_bare_git = (
                local.exists()
                and local.is_dir()
                and (local / "HEAD").is_file()
                and (local / "objects").is_dir()
            )
            if local.exists() and local.is_dir() and not is_bare_git:
                return spec_status_dir(local)
            repo = SpecRepo(project.spec_repo, Path(config.data_dir) / "specs", config.gh_token)
            repo.sync()
            return spec_status_dir(repo.path)
        except Exception as exc:
            return SpecStatus(REQUIRED_INTAKE_FILES, (), REQUIRED_INTAKE_FILES, False, str(exc))

    def require_spec_files_ready(project: Project) -> SpecStatus:
        status = spec_status(project)
        if status.ready:
            return status
        if status.error:
            raise HTTPException(409, f"could not verify spec files: {status.error}")
        raise HTTPException(
            409,
            "missing required spec files: " + ", ".join(status.missing_files),
        )

    def create_intake_conversation(project: Project, prefer_backend: str = "") -> AgentConversation:
        backend, model, _runner_id = trusted_intake_capacity(project.workspace_id, prefer_backend)
        conversation = store.put(
            AgentConversation(
                workspace_id=project.workspace_id,
                project_id=project.id,
                repo=project.spec_repo,
                backend=backend,
                model=model,
                status=ConversationStatus.open,
            )
        )
        project.intake_conversation_id = conversation.id
        project.state = ProjectState.intake
        store.put(project)
        return conversation

    def writable_intake_conversation(project: Project, prefer_backend: str = "") -> AgentConversation:
        existing = (
            store.get(AgentConversation, project.intake_conversation_id)
            if project.intake_conversation_id
            else None
        )
        if existing and existing.status in (ConversationStatus.running, ConversationStatus.finalizing):
            return existing
        if existing and existing.status == ConversationStatus.open:
            return existing
        return create_intake_conversation(project, prefer_backend)

    def accept_project_intake(
        project: Project,
        conversation: AgentConversation | None = None,
    ) -> tuple[AgentConversation, SpecStatus]:
        if conversation and conversation.status in (ConversationStatus.running, ConversationStatus.finalizing):
            raise HTTPException(409, "intake scout is already running")
        status = require_spec_files_ready(project)
        accepted_at = time.time()
        summary = (
            "Accepted durable spec files: "
            + ", ".join(status.present_files)
            + ". Planning can use the spec repo as source of truth."
        )
        if conversation:
            def mark(conv: AgentConversation) -> None:
                conv.status = ConversationStatus.done
                conv.latest_brief = conv.latest_brief or summary
                conv.transcript.append({"role": "system", "text": summary})
                conv.updated_at = accepted_at

            accepted = store.update(AgentConversation, conversation.id, mark) or conversation
        else:
            accepted = store.put(
                AgentConversation(
                    workspace_id=project.workspace_id,
                    project_id=project.id,
                    repo=project.spec_repo,
                    backend="manual",
                    model="",
                    status=ConversationStatus.done,
                    latest_brief=summary,
                    transcript=[{"role": "system", "text": summary}],
                    updated_at=accepted_at,
                )
            )
        project.intake_conversation_id = accepted.id
        project.state = ProjectState.idle
        store.put(project)
        supervisor.wake(
            project.id,
            "Intake accepted from durable spec files. Plan from the spec repo.\n"
            f"Files: {', '.join(status.present_files)}",
        )
        return accepted, status

    def intake_context(conversation: AgentConversation) -> str:
        recent = conversation.transcript[-8:]
        transcript = "\n\n".join(
            f"{item.get('role', 'unknown')}:\n{item.get('text', '').strip()}"
            for item in recent
            if item.get("text", "").strip()
        )
        return "\n".join(
            [
                "Current intake context:",
                "",
                "Latest brief:",
                conversation.latest_brief.strip() or "(none yet)",
                "",
                "Recent transcript:",
                transcript or "(none)",
                "",
            ]
        )

    def intake_prompt(
        project: Project,
        conversation: AgentConversation,
        turn: str,
        user_text: str = "",
    ) -> str:
        if turn == "initial":
            org_context = store.get_org_context(project.workspace_id).strip()
            return "\n".join(
                [
                    "You are Hive's intake scout.",
                    "",
                    "Goal: understand this project well enough that the user can confirm or correct Hive before work starts.",
                    "",
                    "Inspect the repo. Prefer mission.md, iteration.md, and wiki/ over README guesses. "
                    "You may run cheap diagnostic commands. You may browse public docs for external "
                    "packages/APIs/services, but do not leak private repo content.",
                    "Do not commit, push, deploy, send external messages, or create Hive workstreams/tasks.",
                    "",
                    f"Project name: {project.name}",
                    f"Spec/code repo: {project.spec_repo}",
                    f"Member repos: {', '.join(project.member_repos) or '(none)'}",
                    f"Guess propensity: {project.guess_propensity}",
                    "",
                    "Org context:",
                    org_context or "(none)",
                    "",
                    "Return a compact brief with these sections:",
                    "",
                    "Mission:",
                    "The long-term vision.",
                    "",
                    "Next iteration:",
                    "The concrete, verifiable next goal Hive should probably work toward.",
                    "",
                    "Likely next steps:",
                    "3-5 high-level steps, not implementation tasks.",
                    "",
                    "Assumptions:",
                    "Cheap or reasonable assumptions you made instead of asking.",
                    "",
                    "Questions:",
                    "Only questions whose answers would materially change what Hive builds.",
                    "",
                    "Evidence:",
                    "The files, commands, or public sources that shaped your understanding.",
                ]
            )
        if turn == "proceed":
            return (
                intake_context(conversation)
                + "\n"
                "The user chose to proceed with current information and accepts the risk of "
                "wrong assumptions.\n\n"
                "Return a compact updated brief using the current repo/spec context. Do not "
                "edit files, commit, push, or report on file changes. Do not ask more "
                "questions unless work would be impossible rather than merely risky. Clearly "
                "list the assumptions you are making."
            )
        if turn == "write_mission":
            return (
                intake_context(conversation)
                + "\n"
                "Write the durable project-intake files in the spec repo. The canonical "
                "outputs are dedicated files, not markdown sections in this chat.\n\n"
                "Edit or create:\n"
                "- mission.md — the long-term mission and operating principles.\n"
                "- iteration.md — the concrete next iteration goal and acceptance signal.\n\n"
                "You may also update wiki/intake.md and input-log/* if useful for provenance. "
                "Do not modify product code. Commit and push the spec changes. Report the "
                "commit SHA and the files changed."
            )
        return (
            intake_context(conversation)
            + "\n"
            "The user responded during intake:\n\n"
            f"{user_text.strip()}\n\n"
            "Update your understanding. Self-answer minor follow-ups. Return the revised "
            "brief and only the remaining material questions. Do not commit or push yet."
        )

    def queue_intake_turn(
        project: Project,
        conversation: AgentConversation,
        turn: str,
        user_text: str = "",
    ) -> Task:
        if any(
            t.status in (TaskStatus.pending, TaskStatus.running)
            for t in store.list(Task, workspace_id=project.workspace_id, project_id=project.id)
            if t.kind == TaskKind.intake and t.conversation_id == conversation.id
        ):
            raise HTTPException(409, "intake scout is already running")
        task = store.put(
            Task(
                workspace_id=project.workspace_id,
                project_id=project.id,
                workstream_id="",
                repo=conversation.repo,
                kind=TaskKind.intake,
                instructions=intake_prompt(project, conversation, turn, user_text),
                conversation_id=conversation.id,
                conversation_turn=turn,
                session_handle=conversation.session_handle,
                backend=conversation.backend,
                model=conversation.model,
                prompt_versions={"intake": "inline-v1"},
            )
        )

        def mark(conv: AgentConversation) -> None:
            conv.status = ConversationStatus.finalizing if turn == "finalize" else ConversationStatus.running
            conv.last_task_id = task.id
            conv.updated_at = time.time()
            if user_text.strip():
                conv.transcript.append({"role": "user", "text": user_text.strip()})

        updated = store.update(AgentConversation, conversation.id, mark)
        if updated:
            project.intake_conversation_id = updated.id
            project.state = ProjectState.intake
            store.put(project)
        return task

    def record_answer_input_log(
        project: Project, question: Question, answer: str, answered_at: float
    ) -> str:
        stamp = datetime.datetime.fromtimestamp(answered_at, datetime.UTC)
        path = f"input-log/{stamp:%Y-%m-%d-%H%M%S}-{question.id}.md"
        body = "\n".join(
            [
                f"# Clarification answer {question.id}",
                "",
                f"- Answered: {_iso_utc(answered_at)}",
                f"- Project: {project.name} ({project.id})",
                f"- Workstream: {question.workstream_id or 'project-level'}",
                "",
                "## Question",
                "",
                question.text.strip(),
                "",
                "## Answer",
                "",
                answer.strip(),
                "",
            ]
        )
        spec = SpecRepo(
            project.spec_repo,
            Path(config.data_dir or "/tmp/hive-data") / "specs",
            config.gh_token,
        )
        sha = spec.commit_files({path: body}, f"Record clarification answer {question.id}")
        return f"{path} @ {sha[:8]}"

    def file_spec_log_failure(project: Project, question: Question, exc: Exception) -> None:
        escalate(
            store,
            f"Repair spec logging for {project.name}",
            instructions=(
                "Hive saved a clarification answer in the chief DB, but could not "
                "append the raw answer to the spec repo input log.\n\n"
                f"Question: `{question.id}`\n\n"
                f"Spec repo: `{project.spec_repo}`\n\n"
                f"Error:\n\n```\n{type(exc).__name__}: {str(exc)[:1500]}\n```\n\n"
                "Fix spec-repo write access, then ask Hive to distill or replay the answer "
                "from the project question history."
            ),
            project_id=project.id,
            workspace_id=project.workspace_id,
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        supervisor.acquire_leadership()  # raises if another chief is live
        if config.autostart_runner and local_runner is not None:
            status = local_runner.start()
            log.info(
                "%s as %s (log: %s)",
                status["message"],
                status["runner_name"],
                status["log_path"],
            )
        loop_task = asyncio.create_task(supervisor.run_forever())
        try:
            yield
        finally:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
            if local_runner is not None:
                local_runner.stop()
            supervisor.release_leadership()

    app.router.lifespan_context = lifespan
    app.state.supervisor = supervisor
    app.state.store = store
    app.state.auth = auth

    def active_probe_task(resource: Resource) -> Task | None:
        if not resource.last_probe_task_id:
            return None
        task = store.get(Task, resource.last_probe_task_id)
        if (
            task
            and task.workspace_id == resource.workspace_id
            and task.kind == TaskKind.probe
            and task.status == TaskStatus.running
        ):
            return task
        return None

    def queue_probe(resource: Resource, runner: Runner) -> tuple[Task, Resource]:
        if not resource.enabled:
            raise HTTPException(409, "resource is disabled")
        if resource.backend not in runner.backends:
            raise HTTPException(409, "runner no longer advertises this backend")
        if task := active_probe_task(resource):
            return task, resource

        task = store.put(
            Task(
                workspace_id=resource.workspace_id,
                project_id="",
                workstream_id="",
                repo=PROBE_REPO,  # sentinel; the runner builds its own local probe repo
                kind=TaskKind.probe,
                instructions=probe_instructions(resource.backend),
                backend=resource.backend,
                status=TaskStatus.running,
                runner_id=runner.id,
            )
        )
        resource.usability_status = ResourceUsability.probing
        resource.last_probe_at = time.time()
        resource.last_probe_task_id = task.id
        resource.last_probe_text = "Probe queued."
        store.put(resource)
        return task, resource

    def should_auto_probe(
        resource: Resource,
        discovery: BackendDiscoveryInput | None,
        *,
        auto_probe: bool,
        boot: bool,
    ) -> bool:
        if not auto_probe or not resource.enabled:
            return False
        if resource.usability_status == ResourceUsability.probing:
            return False
        if resource.usability_status == ResourceUsability.unknown:
            return True
        if not boot:
            return False
        if not discovery or not discovery.installed or discovery.status != "ok":
            return False
        return not resource.available()

    def local_runner_payload(workspace_id: str, status: dict | None = None) -> dict:
        if local_runner is None:
            return {
                "supported": False,
                "running": False,
                "registered": False,
                "runner_name": "",
                "pid": 0,
                "autostart": False,
                "log_path": "",
                "message": "local runner management is unavailable",
            }
        status = status or local_runner.status()
        status["registered"] = any(
            r.name == status["runner_name"] and r.online()
            for r in store.list(Runner, workspace_id=workspace_id)
        )
        return status

    def _storage_payload() -> dict:
        return storage_info(store, config, blobs)

    task_results = TaskResultProcessor(
        store,
        supervisor,
        config,
        merge_branch_func=lambda repo, head, token, message="": merge_branch(
            repo, head, token, message=message
        ),
        resolve_issue_func=lambda repo, number, comment, token: resolve_issue_on_github(
            repo, number, comment, token
        ),
        file_finding_issue_func=lambda repo_ref, finding, story, token: file_or_update_finding_issue(
            repo_ref, finding, story, token
        ),
        close_story_issue_func=lambda repo_ref, story, token, comment: close_story_issue(
            repo_ref, story, token, comment
        ),
    )

    # ---- auth ---------------------------------------------------------------

    @app.get("/api/auth/me")
    def auth_me(response: Response, ctx: AuthContext = Depends(current)):
        if config.auth_mode == "dev":
            response.set_cookie(
                SESSION_COOKIE,
                auth.session_token(ctx.user),
                max_age=SESSION_TTL_S,
                httponly=True,
                secure=config.public_url.startswith("https://"),
                samesite="lax",
            )
        return {
            "user": ctx.user.model_dump(exclude={"github_access_token"}),
            "workspace": ctx.workspace.model_dump(),
            "auth_mode": config.auth_mode,
            "storage": _storage_payload(),
        }

    @app.get("/api/auth/github/start")
    def github_start():
        return auth.github_start()

    @app.get("/api/auth/github/callback")
    def github_callback(code: str, state: str):
        return auth.github_callback(code, state)

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/github/repos")
    def github_repos(ctx: AuthContext = Depends(current)):
        login, user_token = auth.github_credentials(ctx.user)
        try:
            return list_github_repos(
                github_login=login,
                user_token=ctx.user.github_access_token,
                config_token=config.gh_token,
            )
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"GitHub API error: {exc}") from exc

    @app.get("/api/github/repos/validate")
    def github_validate_repo(ref: str, ctx: AuthContext = Depends(current)):
        from hive.integrations.github_repos import parse_repo_ref, validate_repo

        login, _token = auth.github_credentials(ctx.user)
        try:
            parse_repo_ref(ref)
            return validate_repo(
                ref,
                github_login=login,
                user_token=ctx.user.github_access_token,
                config_token=config.gh_token,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"GitHub API error: {exc}") from exc

    # ---- web API -------------------------------------------------------------

    @app.get("/api/overview")
    def overview(ctx: AuthContext = Depends(current)):
        return build_overview(store, ctx.workspace_id, supervisor.spend_today)

    @app.get("/api/projects")
    def list_projects(include_archived: bool = False, ctx: AuthContext = Depends(current)):
        projects = store.list(Project, workspace_id=ctx.workspace_id)
        if not include_archived:
            projects = [p for p in projects if not p.archived]
        return [p.model_dump() for p in projects]

    def intake_is_done(project: Project) -> bool:
        if not project.intake_conversation_id:
            return False
        conversation = store.get(AgentConversation, project.intake_conversation_id)
        return bool(conversation and conversation.status == ConversationStatus.done)

    @app.post("/api/projects")
    def create_project(body: ProjectCreate, ctx: AuthContext = Depends(current)):
        project = store.put(Project(workspace_id=ctx.workspace_id, name=body.name.strip()))
        return project.model_dump()

    @app.post("/api/projects/{project_id}/start")
    def start_project(
        project_id: str, body: ProjectStart, ctx: AuthContext = Depends(current)
    ):
        project = require_project(project_id, ctx)
        if not project.spec_repo.strip():
            raise HTTPException(400, "spec_repo must be set before starting")
        if not intake_is_done(project):
            raise HTTPException(409, "complete project intake before starting planning")
        note = "Project start requested after approved intake. Plan from the durable spec."
        if body.mission.strip() or body.iteration_goal.strip():
            note += "\n\nLegacy start brief was ignored because intake specs are authoritative."
        supervisor.wake(project_id, note)
        return project.model_dump()

    @app.post("/api/projects/{project_id}/intake/start")
    def start_intake(
        project_id: str,
        body: IntakeStart = IntakeStart(),
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        if not project.spec_repo.strip():
            raise HTTPException(400, "spec_repo must be set before intake")
        if project.intake_conversation_id:
            existing = store.get(AgentConversation, project.intake_conversation_id)
            # An active conversation already owns intake — return it. A failed or
            # done one is a fresh start: mint a new conversation below (the retry
            # path the UI uses to recover from a blocked scout).
            if existing and existing.status in (
                ConversationStatus.open,
                ConversationStatus.running,
                ConversationStatus.finalizing,
            ):
                return existing.model_dump()
        conversation = create_intake_conversation(project, body.backend.strip())
        queue_intake_turn(project, conversation, "initial")
        return store.get(AgentConversation, conversation.id).model_dump()

    @app.post("/api/projects/{project_id}/intake/write-mission")
    def write_mission(
        project_id: str,
        body: IntakeStart = IntakeStart(),
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        if project.autonomy != Autonomy.direct_push:
            raise HTTPException(400, "write mission currently supports direct_push projects only")
        if not project.spec_repo.strip():
            raise HTTPException(400, "spec_repo must be set before writing mission")
        conversation = writable_intake_conversation(project, body.backend.strip())
        task = queue_intake_turn(project, conversation, "write_mission")
        return {
            "conversation": store.get(AgentConversation, conversation.id).model_dump(),
            "task": task.model_dump(),
        }

    @app.post("/api/projects/{project_id}/intake/finalize")
    def finalize_intake(project_id: str, ctx: AuthContext = Depends(current)):
        project = require_project(project_id, ctx)
        conversation = (
            store.get(AgentConversation, project.intake_conversation_id)
            if project.intake_conversation_id
            else None
        )
        conversation, status = accept_project_intake(project, conversation)
        return {"conversation": conversation.model_dump(), "spec_status": status.model_dump()}

    @app.post("/api/projects/{project_id}/repo")
    def create_project_repo(
        project_id: str,
        body: ProjectRepoCreate,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        if project.spec_repo.strip():
            raise HTTPException(409, "project already has a spec repo")
        login, _user_token = auth.github_credentials(ctx.user)
        default_name = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name.strip().lower()).strip("-")
        name = body.name.strip() or default_name or f"hive-project-{project.id}"
        try:
            repo = create_github_repo(
                name,
                private=body.private,
                description=f"Hive project: {project.name}",
                github_login=login,
                user_token=ctx.user.github_access_token,
                config_token=config.gh_token,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:1000] if exc.response is not None else str(exc)
            raise HTTPException(exc.response.status_code if exc.response else 502, detail) from exc
        project.spec_repo = repo["ssh_url"] or repo["clone_url"]
        project.member_repos = [project.spec_repo]
        project.state = ProjectState.intake
        store.put(project)
        return {"project": project.model_dump(), "repo": repo}

    @app.post("/api/conversations/{conversation_id}/message")
    def conversation_message(
        conversation_id: str,
        body: IntakeMessage,
        ctx: AuthContext = Depends(current),
    ):
        conversation = store.get(AgentConversation, conversation_id)
        if not conversation or conversation.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        project = require_project(conversation.project_id, ctx)
        if conversation.role != "intake":
            raise HTTPException(400, "unsupported conversation role")
        if conversation.status == ConversationStatus.failed:
            raise HTTPException(409, "intake conversation failed; start a new intake to retry")
        if conversation.status == ConversationStatus.done:
            raise HTTPException(409, "intake conversation is done")
        action = body.action.strip().lower() or "message"
        if action not in {"message", "proceed", "approve"}:
            raise HTTPException(400, "action must be message, proceed, or approve")
        if action == "message" and not body.message.strip():
            raise HTTPException(400, "message is required")
        if action == "approve":
            conversation, status = accept_project_intake(project, conversation)
            return {"conversation": conversation.model_dump(), "spec_status": status.model_dump()}
        turn = "proceed" if action == "proceed" else "message"
        task = queue_intake_turn(project, conversation, turn, body.message)
        return {"conversation": store.get(AgentConversation, conversation.id).model_dump(), "task": task.model_dump()}

    @app.post("/api/projects/{project_id}/scan-issues")
    def scan_issues(project_id: str, ctx: AuthContext = Depends(current)):
        """Compatibility wrapper: run all currently open issues on the default
        GitHub-issues workstream."""
        project = require_project(project_id, ctx)
        if not project.spec_repo.strip():
            raise HTTPException(400, "spec_repo must be set before scanning")
        workstream = ensure_issue_workstream(store, project)
        run, notes, open_count, queued, downloaded, failed = start_issue_run(
            project,
            workstream,
            IssueRunCreate(scope=IssueRunScope.all_open_now),
        )
        log.info(
            "scan %s: %d open issue(s), %d resolve task(s) started, attachments %d ok / %d failed; changes: %s",
            project_id, open_count, queued, downloaded, failed, "; ".join(notes) or "none",
        )
        return {
            "open_issues": open_count,
            "resolve_queued": queued,
            "attachments_downloaded": downloaded,
            "attachments_failed": failed,
            "changes": notes,
            "run_id": run.id,
        }

    @app.post("/api/projects/{project_id}/issues-preflight")
    def issues_preflight(project_id: str, ctx: AuthContext = Depends(current)):
        """Compatibility wrapper for the default GitHub-issues workstream."""
        project = require_project(project_id, ctx)
        if not project.spec_repo.strip():
            raise HTTPException(400, "spec_repo must be set before preflight")
        workstream = ensure_issue_workstream(store, project)
        return issue_workstream_preflight(project_id, workstream.id, ctx)

    @app.post("/api/projects/{project_id}/workstreams")
    def create_project_workstream(
        project_id: str,
        body: ProjectWorkstreamCreate,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        repo = body.repo.strip() or project.spec_repo
        if not repo.strip():
            raise HTTPException(400, "repo is required")
        if body.kind == ProjectWorkstreamKind.github_issues:
            return ensure_issue_workstream(store, project, repo=repo).model_dump()
        if body.kind == ProjectWorkstreamKind.testing:
            return ensure_testing_workstream(store, project, repo=repo).model_dump()
        raise HTTPException(400, "only GitHub issue and testing workstreams can be created manually")

    @app.patch("/api/projects/{project_id}/workstreams/{workstream_id}")
    def patch_project_workstream(
        project_id: str,
        workstream_id: str,
        body: ProjectWorkstreamPatch,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(400, "title cannot be empty")
            workstream.title = title
        if body.config is not None:
            workstream.config = body.config
        if body.enabled is not None:
            workstream.enabled = body.enabled
            if body.enabled:
                if workstream.status == ProjectWorkstreamStatus.disabled:
                    workstream.status = (
                        ProjectWorkstreamStatus.active
                        if workstream.kind == ProjectWorkstreamKind.iteration
                        else ProjectWorkstreamStatus.idle
                    )
            else:
                workstream.status = ProjectWorkstreamStatus.disabled
        workstream.updated_at = time.time()
        return store.put(workstream).model_dump()

    @app.post("/api/projects/{project_id}/workstreams/{workstream_id}/preflight")
    def issue_workstream_preflight(
        project_id: str,
        workstream_id: str,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        if workstream.kind != ProjectWorkstreamKind.github_issues:
            raise HTTPException(400, "workstream does not read GitHub issues")
        require_enabled_workstream(workstream)
        checks = issue_preflight_checks(project, workstream.repo)
        hard_ok = all(c.ok for c in checks if c.hard)
        runner_task = None
        issue_backend = config.issue_backend or RESOLVE_BACKEND
        if hard_ok and codex_runner_usable(store, project.workspace_id, backend=issue_backend):
            runner_task = create_preflight_task(
                store,
                project,
                workstream_id=workstream.id,
                repo=workstream.repo,
                backend=issue_backend,
                model=config.issue_model,
            ).id
        return {"ok": hard_ok, "checks": checks_payload(checks), "runner_check_task": runner_task}

    @app.post("/api/projects/{project_id}/workstreams/{workstream_id}/sync")
    def sync_workstream_issues(
        project_id: str,
        workstream_id: str,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        notes, open_count, downloaded, failed = sync_issue_workstream(project, workstream)
        return {
            "open_issues": open_count,
            "resolve_queued": 0,
            "attachments_downloaded": downloaded,
            "attachments_failed": failed,
            "changes": notes,
        }

    @app.post("/api/projects/{project_id}/workstreams/{workstream_id}/issue-runs")
    def create_issue_run(
        project_id: str,
        workstream_id: str,
        body: IssueRunCreate,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        run, notes, open_count, queued, downloaded, failed = start_issue_run(project, workstream, body)
        return {
            "run": run.model_dump(),
            "open_issues": open_count,
            "resolve_queued": queued,
            "attachments_downloaded": downloaded,
            "attachments_failed": failed,
            "changes": notes,
        }

    @app.post("/api/issue-runs/{run_id}/cancel")
    def cancel_issue_run(run_id: str, ctx: AuthContext = Depends(current)):
        run = store.get(IssueRun, run_id)
        if not run or run.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        project = require_project(run.project_id, ctx)
        cancelled_tasks = 0
        for task in store.list(
            Task,
            workspace_id=ctx.workspace_id,
            project_id=run.project_id,
            run_id=run.id,
        ):
            if task.status == TaskStatus.pending:
                task.status = TaskStatus.cancelled
                task.result_text = "Cancelled by operator when the issue run was cancelled."
                task.finished_at = time.time()
                store.put(task)
                if task.kind in (TaskKind.resolve, TaskKind.review):
                    cancel_issue_work(store, task)
                cancelled_tasks += 1
            elif task.status == TaskStatus.running:
                if task.delivered:
                    task.cancel_requested = True
                else:
                    task.status = TaskStatus.cancelled
                    task.result_text = "Cancelled by operator before delivery to a runner."
                    task.finished_at = time.time()
                    if task.kind in (TaskKind.resolve, TaskKind.review):
                        cancel_issue_work(store, task)
                store.put(task)
                cancelled_tasks += 1

        run = refresh_issue_run(store, project, run)

        def mark(saved: IssueRun) -> None:
            saved.status = IssueRunStatus.cancelled
            saved.finished_at = saved.finished_at or time.time()
            saved.counts = {**saved.counts, "cancelled_tasks": cancelled_tasks}

        run = store.update(IssueRun, run.id, mark) or run
        return run.model_dump()

    @app.post("/api/projects/{project_id}/workstreams/{workstream_id}/test-refresh")
    def refresh_testing_workstream(
        project_id: str,
        workstream_id: str,
        body: TestRefreshCreate = TestRefreshCreate(),
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        require_testing_workstream(workstream)
        sync_testing_backlog(project, workstream)
        task = queue_refresh_task(
            store,
            project,
            workstream,
            backend=body.backend or config.test_refresh_backend,
            model=body.model or config.test_refresh_model,
        )
        supervisor.wake(project_id, f"Testing refresh task {task.id} was queued.")
        return {"task": task.model_dump()}

    @app.post("/api/projects/{project_id}/workstreams/{workstream_id}/test-episodes")
    def create_test_episode(
        project_id: str,
        workstream_id: str,
        body: TestEpisodeCreate,
        ctx: AuthContext = Depends(current),
    ):
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        require_testing_workstream(workstream)
        if body.scope == TestEpisodeScope.selected and not body.story_keys:
            raise HTTPException(400, "select at least one story")
        sync_testing_backlog(project, workstream)
        episode, task = start_episode(
            store,
            project,
            workstream,
            scope=body.scope,
            selected_story_keys=body.story_keys,
            max_stories=body.max_stories,
            refresh_backend=body.refresh_backend or config.test_refresh_backend,
            refresh_model=body.refresh_model or config.test_refresh_model,
            sweep_backend=body.sweep_backend or config.test_sweep_backend,
            sweep_model=body.sweep_model or config.test_sweep_model,
            confirm_backend=body.confirm_backend or config.test_confirm_backend,
            confirm_model=body.confirm_model or config.test_confirm_model,
        )
        supervisor.wake(project_id, f"Testing episode {episode.id} was created.")
        return {"episode": episode.model_dump(), "refresh_task": task.model_dump()}

    @app.post("/api/test-episodes/{episode_id}/cancel")
    def cancel_test_episode(episode_id: str, ctx: AuthContext = Depends(current)):
        episode = store.get(TestEpisode, episode_id)
        if not episode or episode.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        cancelled_tasks = 0
        for task in store.list(
            Task,
            workspace_id=ctx.workspace_id,
            project_id=episode.project_id,
            run_id=episode.id,
        ):
            if task.status == TaskStatus.pending:
                task.status = TaskStatus.cancelled
                task.result_text = "Cancelled by operator when the testing episode was cancelled."
                task.finished_at = time.time()
                store.put(task)
                cancelled_tasks += 1
            elif task.status == TaskStatus.running:
                if task.delivered:
                    task.cancel_requested = True
                else:
                    task.status = TaskStatus.cancelled
                    task.result_text = "Cancelled by operator before delivery to a runner."
                    task.finished_at = time.time()
                store.put(task)
                cancelled_tasks += 1

        def mark(saved: TestEpisode) -> None:
            saved.status = TestEpisodeStatus.cancelled
            saved.finished_at = saved.finished_at or time.time()
            saved.counts = {**saved.counts, "cancelled_tasks": cancelled_tasks}

        episode = store.update(TestEpisode, episode.id, mark) or episode
        return episode.model_dump()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, ctx: AuthContext = Depends(current)):
        project = require_project(project_id, ctx)
        work_items = store.list(Workstream, workspace_id=ctx.workspace_id, project_id=project_id)
        human_todos = [
            t.model_dump()
            for t in store.list(HumanTask, workspace_id=ctx.workspace_id, project_id=project_id)
        ]
        return {
            "project": project.model_dump(),
            "workstreams": [
                w.model_dump()
                for w in project_workstreams(store, project)
            ],
            "work_items": [
                w.model_dump()
                for w in work_items
            ],
            "tasks": [
                t.model_dump()
                for t in store.list(
                    Task, workspace_id=ctx.workspace_id, project_id=project_id, limit=50
                )
            ],
            "questions": [
                q.model_dump()
                for q in store.list(Question, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "human_todos": human_todos,
            "human_tasks": human_todos,
            "conversations": [
                c.model_dump()
                for c in store.list(AgentConversation, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "issue_runs": [
                r.model_dump()
                for r in store.list(IssueRun, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "stories": [
                s.model_dump()
                for s in store.list(Story, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "findings": [
                f.model_dump()
                for f in store.list(Finding, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "test_episodes": [
                e.model_dump()
                for e in store.list(TestEpisode, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "directives": [
                d.model_dump()
                for d in store.list(Directive, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "checkouts": [c.model_dump() for c in project_checkouts(ctx.workspace_id, project)],
            "spend_today": supervisor.spend_today(project_id),
        }

    def project_repos(project: Project) -> list[str]:
        """The repos a project physically lives in: spec home plus members."""
        repos = [project.spec_repo, *project.member_repos]
        seen, out = set(), []
        for repo in repos:
            repo = (repo or "").strip()
            if repo and repo not in seen:
                seen.add(repo)
                out.append(repo)
        return out

    def project_checkouts(workspace_id: str, project: Project) -> list[Checkout]:
        """Checkouts of this project's repos, across every machine. Checkouts are
        keyed by (machine, repo) and shared across projects on the same repo, so
        we filter the workspace's checkouts by the project's repo set. Matching is
        canonical so a runner's `owner/repo.git` origin joins the stored
        `https://github.com/owner/repo`."""
        repos = {canonical_repo(r) for r in project_repos(project)}
        return [
            c
            for c in store.list(Checkout, workspace_id=workspace_id)
            if canonical_repo(c.repo) in repos
        ]

    def suggest_executor(project: Project) -> tuple[str, str, str, str]:
        """Preview routing for a directive: pick a plausible (backend, model,
        machine_id) over live capacity, with a one-line rationale. This is a
        stub — the real triage/dispatch engine is unbuilt (see
        wiki/project-launchpad.md). Returns ("", "", "", note) when nothing is
        online so the UI can say so honestly."""
        available = [
            r
            for r in store.list(Resource, workspace_id=project.workspace_id)
            if r.available()
        ]
        if not available:
            return "", "", "", "No online agent right now — routing will wait for capacity."
        pick = min(available, key=lambda r: (r.total_tasks, r.total_cost_usd))
        machine = store.get(Machine, pick.machine_id) if pick.machine_id else None
        where = machine.name if machine else "an available machine"
        return (
            pick.backend,
            "",
            pick.machine_id,
            f"Preview: would run on {pick.backend} on {where} (least-loaded online agent).",
        )

    @app.post("/api/projects/{project_id}/directives")
    def create_directive(
        project_id: str, body: DirectiveCreate, ctx: AuthContext = Depends(current)
    ):
        project = require_project(project_id, ctx)
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "directive text cannot be empty")
        backend, model, machine_id, note = suggest_executor(project)
        directive = Directive(
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            text=text,
            status=DirectiveStatus.awaiting_executor if backend else DirectiveStatus.triaging,
            suggested_backend=backend,
            suggested_model=model,
            suggested_machine_id=machine_id,
            routing_note=note,
        )
        store.put(directive)
        return directive.model_dump()

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch, ctx: AuthContext = Depends(current)):
        project = require_project(project_id, ctx)
        updates = body.model_dump(exclude_none=True)
        note = updates.pop("new_iteration_note", None)
        if "name" in updates:
            updates["name"] = updates["name"].strip()
            if not updates["name"]:
                raise HTTPException(400, "name cannot be empty")
        if "spec_repo" in updates and not updates["spec_repo"].strip():
            if store.list(Workstream, workspace_id=ctx.workspace_id, project_id=project_id):
                raise HTTPException(400, "cannot clear spec_repo after work has started")
        for key, value in updates.items():
            setattr(project, key, value)
        if note is not None:
            project.goal_complete = False
            project.goal_complete_note = ""
            store.put(project)
            supervisor.wake(
                project_id,
                f"New iteration goal set by the user (authoritative): {note}\n"
                "Your FIRST action must be commit_to_spec: archive the prior iteration.md to "
                "iterations/ with a one-line outcome, then write this goal into iteration.md. "
                "Only then plan workstreams.",
            )
        store.put(project)
        return project.model_dump()

    @app.post("/api/questions/{question_id}/answer")
    def answer_question(
        question_id: str, body: AnswerBody, ctx: AuthContext = Depends(current)
    ):
        question = store.get(Question, question_id)
        if not question or question.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        project = require_project(question.project_id, ctx)
        answered_at = time.time()
        input_log_note = ""
        if can_write_spec_repo(project):
            try:
                input_log_note = (
                    "Chief already appended the raw answer to "
                    f"{record_answer_input_log(project, question, body.answer, answered_at)}.\n"
                )
            except Exception as exc:
                log.warning("failed to append question %s to spec input-log: %s", question.id, exc)
                file_spec_log_failure(project, question, exc)
                input_log_note = (
                    "Chief could not append the raw answer to input-log automatically; "
                    "a human todo was filed with the write error.\n"
                )
        question.status = QuestionStatus.answered
        question.answer = body.answer
        question.answered_at = answered_at
        store.put(question)
        supervisor.wake(
            question.project_id,
            f"User answered question {question.id}.\nQ: {question.text}\nA: {body.answer}\n"
            f"{input_log_note}"
            "Distill this into the wiki/spec and continue.",
        )
        return question.model_dump()

    @app.post("/api/questions/{question_id}/dismiss")
    def dismiss_question(question_id: str, ctx: AuthContext = Depends(current)):
        question = store.get(Question, question_id)
        if not question or question.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        question.status = QuestionStatus.dismissed
        store.put(question)
        supervisor.wake(
            question.project_id,
            f"User dismissed question {question.id} without answering. If a workstream "
            "parked on it, decide whether to reactivate it or leave it parked.",
        )
        return question.model_dump()

    @app.post("/api/feedback")
    def add_feedback(body: FeedbackBody, ctx: AuthContext = Depends(current)):
        require_project(body.project_id, ctx)
        return store.put(Feedback(workspace_id=ctx.workspace_id, **body.model_dump())).model_dump()

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str, ctx: AuthContext = Depends(current)):
        task = store.get(Task, task_id)
        if not task or task.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        return task.model_dump()

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, ctx: AuthContext = Depends(current)):
        task = store.get(Task, task_id)
        if not task or task.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        if task.status == TaskStatus.pending:
            # Never dispatched: drop it outright.
            task.status = TaskStatus.cancelled
            task.result_text = "Cancelled by operator before dispatch."
            task.finished_at = time.time()
            store.put(task)
            cancel_issue_work(store, task)
            supervisor.wake(task.project_id, f"Task {task.id} was cancelled before it ran.")
        elif task.status == TaskStatus.running:
            if task.delivered:
                # Cooperative: the runner polls this flag and stops the agent.
                task.cancel_requested = True
            else:
                task.status = TaskStatus.cancelled
                task.result_text = "Cancelled by operator before delivery to a runner."
                task.finished_at = time.time()
            store.put(task)
        return task.model_dump()

    @app.post("/api/tasks/{task_id}/trace")
    async def upload_trace(
        task_id: str,
        request: Request,
        workspace_id: str = Depends(runner_auth),
    ):
        task = store.get(Task, task_id)
        if not task or task.workspace_id != workspace_id:
            raise HTTPException(404)
        if blobs is None:
            raise HTTPException(503, "no blob store configured")
        key = f"workspaces/{workspace_id}/traces/{task_id}.jsonl"
        blobs.put(key, await request.body())
        task.trace_blob = key
        store.put(task)
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/artifacts/{name:path}")
    async def upload_artifact(
        task_id: str,
        name: str,
        request: Request,
        workspace_id: str = Depends(runner_auth),
    ):
        task = store.get(Task, task_id)
        if not task or task.workspace_id != workspace_id:
            raise HTTPException(404)
        if blobs is None:
            raise HTTPException(503, "no blob store configured")
        try:
            clean = safe_artifact_name(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        key = artifact_key(workspace_id, task_id, clean)
        blobs.put(key, await request.body())
        if clean not in task.artifact_blobs:
            task.artifact_blobs.append(clean)
            store.put(task)
        return {"ok": True, "name": clean}

    @app.get("/api/tasks/{task_id}/attachments/{name}")
    def get_attachment(task_id: str, name: str, workspace_id: str = Depends(runner_auth)):
        """Runner-auth: serve an issue's image (downloaded on the chief at
        scan time) so the worker can materialize `.hive/issue-<n>/attachments/`
        without its own GitHub credentials."""
        task = store.get(Task, task_id)
        if not task or task.workspace_id != workspace_id or blobs is None:
            raise HTTPException(404)
        data = blobs.get(attachment_key(workspace_id, task.project_id, task.issue_number, name))
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="application/octet-stream")

    @app.get("/api/tasks/{task_id}/artifacts/{name:path}")
    def get_artifact(task_id: str, name: str, ctx: AuthContext = Depends(current)):
        task = store.get(Task, task_id)
        if not task or task.workspace_id != ctx.workspace_id or blobs is None:
            raise HTTPException(404)
        try:
            clean = safe_artifact_name(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if clean not in task.artifact_blobs:
            raise HTTPException(404)
        data = blobs.get(artifact_key(ctx.workspace_id, task_id, clean))
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="application/octet-stream")

    @app.get("/api/tasks/{task_id}/trace")
    def get_trace(task_id: str, ctx: AuthContext = Depends(current)):
        task = store.get(Task, task_id)
        if not task or task.workspace_id != ctx.workspace_id or not task.trace_blob or blobs is None:
            raise HTTPException(404)
        data = blobs.get(task.trace_blob)
        if data is None:
            raise HTTPException(404)
        return PlainTextResponse(data, media_type="application/x-ndjson")

    @app.get("/api/resources")
    def resources(ctx: AuthContext = Depends(current)):
        machines = store.list(Machine, workspace_id=ctx.workspace_id)
        runner_list = store.list(Runner, workspace_id=ctx.workspace_id)
        resource_list = store.list(Resource, workspace_id=ctx.workspace_id)
        subs = store.list(Subscription, workspace_id=ctx.workspace_id)
        runners = {r.id: r for r in runner_list}

        # Flat lists serve the CLI and per-project availability checks; `cards`
        # is the same grouping the home dashboard uses (see hive.control.capacity).
        return {
            "machines": [m.model_dump() for m in machines],
            "runners": [{**r.model_dump(), "online": r.online()} for r in runner_list],
            "resources": [
                {**res.model_dump(), "available": resource_available(res, runners.get(res.runner_id))}
                for res in resource_list
            ],
            "cards": machine_cards(group_machines(machines, runner_list, resource_list)),
            "subscription_candidates": subscription_candidates(subs, resource_list, runner_list),
            "local_runner": local_runner_payload(ctx.workspace_id),
        }

    @app.delete("/api/machines/{machine_id}")
    def forget_machine(machine_id: str, ctx: AuthContext = Depends(current)):
        """Remove a machine the user no longer recognizes, with its runners,
        resources, and checkouts. A live runner re-registers and reappears; this
        is for pruning hosts that are gone for good (old chiefs, retired laptops)."""
        machine = store.get(Machine, machine_id)
        if not machine or machine.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        runner_ids = {
            r.id
            for r in store.list(Runner, workspace_id=ctx.workspace_id)
            if r.machine_id == machine_id
        }
        for resource in store.list(Resource, workspace_id=ctx.workspace_id):
            if resource.machine_id == machine_id or resource.runner_id in runner_ids:
                store.delete(Resource, resource.id)
        for runner_id in runner_ids:
            store.delete(Runner, runner_id)
        for checkout in store.list(Checkout, workspace_id=ctx.workspace_id, machine_id=machine_id):
            store.delete(Checkout, checkout.id)
        store.delete(Machine, machine_id)
        return {"ok": True, "forgotten": machine_id}

    @app.patch("/api/local-runner")
    def update_local_runner(body: LocalRunnerPatch, ctx: AuthContext = Depends(current)):
        if local_runner is None:
            raise HTTPException(404, "local runner management is unavailable")
        status = local_runner.set_autostart(body.autostart)
        if body.autostart and not status["running"]:
            status = local_runner.start()
        return local_runner_payload(ctx.workspace_id, status)

    @app.post("/api/local-runner/start")
    def start_local_runner(ctx: AuthContext = Depends(current)):
        if local_runner is None:
            raise HTTPException(404, "local runner management is unavailable")
        for runner in store.list(Runner, workspace_id=ctx.workspace_id):
            if runner.name == local_runner.runner_name and runner.online():
                status = local_runner.status(message="local runner already registered")
                return local_runner_payload(ctx.workspace_id, status)
        status = local_runner.start()
        return local_runner_payload(ctx.workspace_id, status)

    @app.post("/api/resources/{resource_id}/probe")
    def probe_resource(resource_id: str, ctx: AuthContext = Depends(current)):
        resource = store.get(Resource, resource_id)
        if not resource or resource.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        runner = store.get(Runner, resource.runner_id)
        if not runner or runner.workspace_id != ctx.workspace_id or not runner.online():
            raise HTTPException(409, "runner is offline")
        task, resource = queue_probe(resource, runner)
        return {"task": task.model_dump(), "resource": {**resource.model_dump(), "available": resource.available()}}

    @app.patch("/api/resources/{resource_id}")
    def update_resource(
        resource_id: str,
        body: ResourcePatch,
        ctx: AuthContext = Depends(current),
    ):
        resource = store.get(Resource, resource_id)
        if not resource or resource.workspace_id != ctx.workspace_id:
            raise HTTPException(404)

        def mutate(resource: Resource) -> None:
            if body.enabled is not None:
                resource.enabled = body.enabled
                if body.enabled:
                    resource.disabled_reason = ""
                else:
                    reason = body.disabled_reason.strip()
                    resource.disabled_reason = reason or "Disabled by operator."

        updated = store.update(Resource, resource_id, mutate)
        if updated is None:
            raise HTTPException(404)
        if body.enabled is False:
            complete_resource_login_todos(store, updated)
        return {**updated.model_dump(), "available": updated.available()}

    @app.get("/api/subscriptions")
    def list_subscriptions(ctx: AuthContext = Depends(current)):
        return [
            s.model_dump() for s in store.list(Subscription, workspace_id=ctx.workspace_id)
        ]

    @app.post("/api/subscriptions")
    def create_subscription(body: dict, ctx: AuthContext = Depends(current)):
        return store.put(
            Subscription(
                workspace_id=ctx.workspace_id,
                provider=body["provider"],
                plan=body.get("plan", ""),
                licensing_mode=LicensingMode(body.get("licensing_mode", "unknown")),
                notes=body.get("notes", ""),
            )
        ).model_dump()

    @app.delete("/api/subscriptions/{sub_id}")
    def delete_subscription(sub_id: str, ctx: AuthContext = Depends(current)):
        sub = store.get(Subscription, sub_id)
        if not sub or sub.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        store.delete(Subscription, sub_id)
        return {"ok": True}

    @app.get("/api/human-todos")
    @app.get("/api/human-tasks")
    def list_human_todos(ctx: AuthContext = Depends(current)):
        return [t.model_dump() for t in store.list(HumanTask, workspace_id=ctx.workspace_id)]

    @app.post("/api/human-todos")
    @app.post("/api/human-tasks")
    def create_human_todo(body: dict, ctx: AuthContext = Depends(current)):
        project_id = body.get("project_id", "")
        if project_id:
            require_project(project_id, ctx)
        return store.put(
            HumanTask(
                workspace_id=ctx.workspace_id,
                project_id=project_id,
                title=body["title"],
                instructions=body.get("instructions", ""),
            )
        ).model_dump()

    @app.post("/api/human-todos/{todo_id}/done")
    @app.post("/api/human-tasks/{todo_id}/done")
    def complete_human_todo(todo_id: str, ctx: AuthContext = Depends(current)):
        task = store.get(HumanTask, todo_id)
        if not task or task.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        task.status = HumanTaskStatus.done
        task.done_at = time.time()
        store.put(task)
        sync_landing_failure_human_task(
            store,
            task,
            config,
            issue_is_closed_func=lambda repo, number, token: issue_is_closed(repo, number, token),
        )
        # The action may have unblocked work (a runner login, a granted access).
        # Wake the owning project, or every project for an org-wide todo.
        note = f"Human todo '{task.title}' was completed. Re-evaluate work that waited on it."
        if task.project_id:
            supervisor.wake(task.project_id, note)
        else:
            for project in store.list(Project, workspace_id=ctx.workspace_id):
                supervisor.wake(project.id, note)
        return task.model_dump()

    @app.get("/api/org-context")
    def get_org_context(ctx: AuthContext = Depends(current)):
        return {"text": store.get_org_context(ctx.workspace_id)}

    @app.put("/api/org-context")
    def set_org_context(body: dict, ctx: AuthContext = Depends(current)):
        store.set_org_context(body.get("text", ""), ctx.workspace_id)
        return {"ok": True}

    @app.get("/api/storage")
    def get_storage(ctx: AuthContext = Depends(current)):
        return _storage_payload()

    # ---- runner protocol -------------------------------------------------------

    @app.post("/api/runners/register")
    def register_runner(body: RunnerRegister, workspace_id: str = Depends(runner_auth)):
        machine = ensure_machine(
            store,
            workspace_id,
            name=body.machine_name or body.name,
            machine_id=body.machine_id,
            hostname=body.name,
            kind="runner",
            machine_type=body.machine_type,
            machine_os=body.machine_os,
            machine_arch=body.machine_arch,
            device_kind=body.machine_kind,
        )
        existing = next(
            (r for r in store.list(Runner, workspace_id=workspace_id) if r.name == body.name),
            None,
        )
        runner = existing or Runner(
            workspace_id=workspace_id,
            machine_id=machine.id,
            name=body.name,
        )
        runner.workspace_id = workspace_id
        runner.machine_id = machine.id
        runner.backends = body.backends
        runner.capabilities = sorted(set(body.capabilities))
        runner.last_seen = time.time()
        store.put(runner)

        if body.boot:
            # A booting daemon executes nothing: whatever was in flight on this
            # runner died with the previous process — requeue it before queuing
            # fresh startup probes below.
            def requeue(task: Task) -> None:
                if task.kind == TaskKind.probe:
                    task.status = TaskStatus.failed
                    task.is_error = True
                    task.result_text = "Probe interrupted because the runner rebooted."
                    task.finished_at = time.time()
                else:
                    task.status = TaskStatus.pending
                    task.runner_id = ""
                    task.delivered = False

            for task in store.list(
                Task, workspace_id=workspace_id, status=TaskStatus.running, runner_id=runner.id
            ):
                updated = store.update(Task, task.id, requeue)
                if updated and updated.kind == TaskKind.probe:
                    for resource in store.list(
                        Resource,
                        workspace_id=workspace_id,
                        runner_id=runner.id,
                        backend=updated.backend,
                    ):
                        if resource.last_probe_task_id == updated.id:
                            resource.usability_status = ResourceUsability.unknown
                            resource.last_probe_text = updated.result_text
                            store.put(resource)
                    log.info("failed probe %s after runner %s reboot", task.id, runner.name)
                else:
                    log.info("requeued task %s after runner %s reboot", task.id, runner.name)

        discovery_by_name = {d.name: d for d in body.discoveries}
        resources_by_pair = {
            (r.machine_id or r.runner_id, r.backend): r
            for r in store.list(Resource, workspace_id=workspace_id)
        }

        def apply_discovery(resource: Resource, discovery: BackendDiscoveryInput) -> None:
            resource.discovery_status = discovery.status
            resource.discovery_text = discovery.message
            resource.discovered_at = time.time()
            resource.cli_path = discovery.path
            resource.cli_version = discovery.version

        def apply_capabilities(resource: Resource) -> None:
            caps = set(body.capabilities)
            resource.browser_status = (
                ResourceUsability.usable if "browser" in caps else ResourceUsability.unknown
            )
            resource.browser_probe_at = time.time() if "browser" in caps else resource.browser_probe_at
            resource.browser_probe_text = "Runner advertised browser capability." if "browser" in caps else resource.browser_probe_text
            resource.docker_status = (
                ResourceUsability.usable if "docker" in caps else ResourceUsability.unknown
            )
            resource.docker_probe_at = time.time() if "docker" in caps else resource.docker_probe_at
            resource.docker_probe_text = "Runner advertised docker capability." if "docker" in caps else resource.docker_probe_text

        for backend in body.backends:
            resource = (
                resources_by_pair.get((machine.id, backend))
                or resources_by_pair.get((runner.id, backend))
                or Resource(
                    workspace_id=workspace_id,
                    machine_id=machine.id,
                    runner_id=runner.id,
                    backend=backend,
                )
            )
            resource.workspace_id = workspace_id
            resource.machine_id = machine.id
            resource.runner_id = runner.id
            if discovery := discovery_by_name.get(backend):
                apply_discovery(resource, discovery)
            apply_capabilities(resource)
            store.put(resource)
            resources_by_pair[(machine.id, backend)] = resource
            if should_auto_probe(
                resource,
                discovery_by_name.get(backend),
                auto_probe=body.auto_probe,
                boot=body.boot,
            ):
                queue_probe(resource, runner)

        for discovery in body.discoveries:
            if discovery.installed:
                continue
            resource = resources_by_pair.get((machine.id, discovery.name))
            if resource:
                apply_discovery(resource, discovery)
                store.put(resource)

        upsert_checkouts(workspace_id, machine.id, body.checkouts)

        return {"runner_id": runner.id, "machine_id": machine.id}

    def upsert_checkouts(
        workspace_id: str, machine_id: str, reports: list[CheckoutReport]
    ) -> None:
        """Record the git facts the runner observed for each repo on this
        machine. Keyed by (machine, repo); the runner is authoritative for its
        own host, so a fresh report replaces the prior one."""
        existing = {
            c.repo: c
            for c in store.list(Checkout, workspace_id=workspace_id, machine_id=machine_id)
        }
        for report in reports:
            checkout = existing.get(report.repo) or Checkout(
                workspace_id=workspace_id, machine_id=machine_id, repo=report.repo
            )
            checkout.exists = report.exists
            checkout.head_sha = report.head_sha
            checkout.branch = report.branch
            checkout.ahead = report.ahead
            checkout.behind = report.behind
            checkout.dirty = report.dirty
            checkout.last_reported_at = time.time()
            store.put(checkout)

    @app.post("/api/runners/{runner_id}/poll")
    async def poll(runner_id: str, workspace_id: str = Depends(runner_auth)):
        runner = store.get(Runner, runner_id)
        if not runner or runner.workspace_id != workspace_id:
            raise HTTPException(404, "unknown runner — re-register")
        deadline = time.monotonic() + RUNNER_POLL_WAIT_S
        while True:
            runner.last_seen = time.time()
            store.put(runner)
            for task in store.list(
                Task, workspace_id=workspace_id, status=TaskStatus.running, runner_id=runner_id
            ):
                if not task.delivered:
                    task.delivered = True
                    store.put(task)
                    return {"task": task.model_dump()}
            if time.monotonic() > deadline:
                return {"task": None}
            try:
                await asyncio.sleep(RUNNER_POLL_SLEEP_S)
            except asyncio.CancelledError:
                log.info("runner poll for %s cancelled during shutdown", runner_id)
                return {"task": None}

    @app.post("/api/tasks/{task_id}/result")
    def task_result(
        task_id: str,
        body: TaskResult,
        workspace_id: str = Depends(runner_auth),
    ):
        try:
            return task_results.handle(task_id, body, workspace_id)
        except LookupError:
            raise HTTPException(404)

    return app


def mount_spa(app: FastAPI, web_dir: Path) -> None:
    if not web_dir.is_dir():
        return

    cache_headers = {
        # Local operators expect `hive run` to serve the freshly built UI.
        # Avoid browser heuristics reusing an older SPA shell or bundle.
        "Cache-Control": "no-store",
    }

    @app.get("/{path:path}")
    def spa(path: str):
        # Serve built assets; anything else falls back to the SPA shell so
        # deep links like /p/<id> work. /api routes are matched first.
        target = web_dir / path
        if path and target.is_file():
            return FileResponse(target, headers=cache_headers)
        return FileResponse(web_dir / "index.html", headers=cache_headers)


def production_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = Config.from_env()
    from hive.persistence.storage import make_blob_store, make_store

    store = make_store(config)
    blobs = make_blob_store(config)
    from hive.control.orchestrator import Orchestrator

    orchestrator = Orchestrator(store, blobs, config)
    supervisor = Supervisor(
        store,
        orchestrator.invoke,
        workspace_id=config.workspace_id,
        machine_name=config.machine_name,
    )
    from hive.runner.local import LocalRunnerManager

    app = create_app(
        store,
        supervisor,
        config,
        blobs=blobs,
        local_runner=LocalRunnerManager(config),
    )

    mount_spa(app, Path(os.environ.get("HIVE_WEB_DIST", "web/dist")))
    return app
