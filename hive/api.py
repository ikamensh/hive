"""FastAPI chief: web API + runner protocol + app wiring.

Build with `create_app()` for production (env config) or pass explicit pieces
in tests. Web endpoints are unauthenticated (the service sits behind
Tailscale); runner endpoints require the shared runner token.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import re
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from hive._integrations.auth import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    AuthContext,
    AuthManager,
)
from hive.config.settings import Config
from hive._control import clarifications, intake
from hive._integrations.github_repos import all_repos as list_github_repos
from hive._integrations.github_repos import create_repo as create_github_repo
from hive._integrations.specrepo import SpecRepo
from hive._workstreams.issues import (
    advance_issues,
    attachment_key,
    delete_branch,
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
from hive._workstreams.ci import check_and_autofix
from hive._workstreams.preflight import (
    checks_payload,
    codex_runner_usable,
    create_preflight_task,
    preflight_checks,
)
from hive._workstreams.testing import (
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
    Resource,
    Runner,
    Story,
    Subscription,
    Task,
    TaskStatus,
    TestEpisode,
    TestEpisodeScope,
    TestEpisodeStatus,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)
from hive.persistence.storage import storage_info
from hive._control.capacity import (
    group_machines,
    machine_cards,
    resource_available,
    subscription_candidates,
)
from hive._control.overview import build_overview
from hive._control.supervisor import Supervisor
from hive.version import get_version, version_payload
from hive.runner import registration
from hive.runner.registration import RunnerRegister
from hive.runner._task_results import (
    TaskResult,
    TaskResultProcessor,
    cancel_issue_work,
    complete_resource_login_todos,
    sync_landing_failure_human_task,
)

log = logging.getLogger("hive.api")

RUNNER_POLL_WAIT_S = 5.0
RUNNER_POLL_SLEEP_S = 1.0


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
    ci_autofix: bool | None = None
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


class CiWebhookPayload(BaseModel):
    """A CI signal forwarded by a repo's GitHub Actions workflow (see
    deploy/ci-autofix.github-workflow.yml). Bearer-authed, not HMAC — the
    forwarder is trusted and posts exactly the fields we need."""

    repo: str  # "owner/repo" (github.repository)
    ref: str = ""  # the failing run's head branch
    event: str = "ci_failure"
    run_id: int = 0
    details: str = ""  # tail of the failing-run logs, embedded into the issue


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


class DirectiveCreate(BaseModel):
    text: str


class ResourcePatch(BaseModel):
    enabled: bool | None = None
    disabled_reason: str = ""


class LocalRunnerPatch(BaseModel):
    autostart: bool


def create_app(store, supervisor: Supervisor, config: Config, blobs=None, local_runner=None) -> FastAPI:
    app = FastAPI(title=f"hive {get_version()}")
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
        return preflight_checks(store, config, project, repo=repo)

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
        delete_branch_func=lambda repo, branch, token: delete_branch(repo, branch, token),
        file_finding_issue_func=lambda repo_ref, finding, story, token: file_or_update_finding_issue(
            repo_ref, finding, story, token
        ),
        close_story_issue_func=lambda repo_ref, story, token, comment: close_story_issue(
            repo_ref, story, token, comment
        ),
    )

    @app.get("/api/version")
    def version():
        return version_payload()

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
            "version": version_payload(),
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
        from hive._integrations.github_repos import parse_repo_ref, validate_repo

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
        if not intake.is_done(store, project):
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
        conversation = intake.create_conversation(store, project, body.backend.strip())
        intake.queue_turn(store, project, conversation, "initial")
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
        conversation = intake.writable_conversation(store, project, body.backend.strip())
        task = intake.queue_turn(store, project, conversation, "write_mission")
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
        conversation, status = intake.accept(store, supervisor, config, project, conversation)
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
            conversation, status = intake.accept(store, supervisor, config, project, conversation)
            return {"conversation": conversation.model_dump(), "spec_status": status.model_dump()}
        turn = "proceed" if action == "proceed" else "message"
        task = intake.queue_turn(store, project, conversation, turn, body.message)
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

    def cancel_one_task(task: Task, *, pending_msg: str, predelivery_msg: str) -> bool:
        """Apply the operator-cancel transition to one task. A pending or
        not-yet-delivered task is stopped outright; a delivered task is only
        flagged (`cancel_requested`) for the runner to honor cooperatively. A
        hard-cancelled resolve/review task releases its issue workstream
        (`cancel_issue_work` is a no-op for other kinds). Returns True if the
        task was still active (now cancelled or flagged), False if already
        terminal."""
        if task.status == TaskStatus.pending:
            task.status = TaskStatus.cancelled
            task.result_text = pending_msg
            task.finished_at = time.time()
            store.put(task)
            cancel_issue_work(store, task)
            return True
        if task.status == TaskStatus.running:
            if task.delivered:
                task.cancel_requested = True
                store.put(task)
                return True
            task.status = TaskStatus.cancelled
            task.result_text = predelivery_msg
            task.finished_at = time.time()
            store.put(task)
            cancel_issue_work(store, task)
            return True
        return False

    @app.post("/api/issue-runs/{run_id}/cancel")
    def cancel_issue_run(run_id: str, ctx: AuthContext = Depends(current)):
        run = store.get(IssueRun, run_id)
        if not run or run.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        project = require_project(run.project_id, ctx)
        cancelled_tasks = sum(
            cancel_one_task(
                task,
                pending_msg="Cancelled by operator when the issue run was cancelled.",
                predelivery_msg="Cancelled by operator before delivery to a runner.",
            )
            for task in store.list(
                Task,
                workspace_id=ctx.workspace_id,
                project_id=run.project_id,
                run_id=run.id,
            )
        )

        run = refresh_issue_run(store, project, run)

        def mark(saved: IssueRun) -> None:
            saved.status = IssueRunStatus.cancelled
            saved.finished_at = saved.finished_at or time.time()
            saved.counts = {**saved.counts, "cancelled_tasks": cancelled_tasks}

        run = store.update(IssueRun, run.id, mark) or run
        return run.model_dump()

    @app.post("/api/projects/{project_id}/workstreams/{workstream_id}/check-ci")
    def check_workstream_ci(
        project_id: str,
        workstream_id: str,
        ctx: AuthContext = Depends(current),
    ):
        """Check this repo's default-branch CI; if red, file a GitHub issue and
        hand it to the issue-solving pipeline (the same resolve→review→land path
        that fixes human-filed issues)."""
        project = require_project(project_id, ctx)
        workstream = require_project_workstream(project, workstream_id, ctx)
        if workstream.kind != ProjectWorkstreamKind.github_issues:
            raise HTTPException(400, "workstream does not read GitHub issues")
        require_enabled_workstream(workstream)
        result = check_and_autofix(
            store,
            project,
            workstream,
            config.gh_token,
            issue_backend=config.issue_backend,
            issue_model=config.issue_model,
        )
        if result.resolve_queued:
            supervisor.wake(project_id, f"CI red on {workstream.repo}; queued a fix for #{result.filed_issue}.")
        return result.model_dump()

    @app.post("/api/ci/webhook")
    def ci_webhook(body: CiWebhookPayload, authorization: str = Header(default="")):
        """Real-time CI signal forwarded by a repo's GitHub Actions workflow
        (`deploy/ci-autofix.github-workflow.yml`). Bearer-authed by
        `HIVE_GITHUB_WEBHOOK_SECRET`; for every ci_autofix project that owns the
        repo it runs the same file→reconcile→advance path as the periodic poll
        (which `fetch_ci_status` re-confirms, so a feature-branch failure that
        left the default branch green is a no-op)."""
        secret = config.github_webhook_secret.strip()
        if not secret:
            raise HTTPException(503, "CI webhook is disabled; set HIVE_GITHUB_WEBHOOK_SECRET")
        presented = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(presented, secret):
            raise HTTPException(401, "bad webhook secret")
        if body.event not in ("ci_failure", "workflow_run", ""):
            return {"matched": 0, "skipped": f"event {body.event!r} not handled"}
        target = canonical_repo(body.repo)
        if not target:
            raise HTTPException(400, "repo is required")
        results: list[dict] = []
        for project in store.list(Project, workspace_id=config.workspace_id):
            if project.archived or project.paused or not project.ci_autofix:
                continue
            repos = {
                canonical_repo(r): r
                for r in [project.spec_repo, *project.member_repos]
                if r.strip()
            }
            repo_url = repos.get(target)
            if not repo_url:
                continue
            try:
                workstream = ensure_issue_workstream(store, project, repo=repo_url)
                if not workstream.enabled:
                    continue
                result = check_and_autofix(
                    store,
                    project,
                    workstream,
                    config.gh_token,
                    issue_backend=config.issue_backend,
                    issue_model=config.issue_model,
                    details=body.details,
                )
                if result.resolve_queued:
                    supervisor.wake(
                        project.id,
                        f"CI red on {repo_url} (webhook); queued a fix for #{result.filed_issue}.",
                    )
                results.append(
                    {"project_id": project.id, "conclusion": result.conclusion, "filed_issue": result.filed_issue}
                )
            except Exception as exc:
                log.exception("CI webhook handling failed for project %s repo %s", project.id, repo_url)
                results.append({"project_id": project.id, "error": str(exc)})
        return {"matched": len(results), "results": results}

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
        cancelled_tasks = sum(
            cancel_one_task(
                task,
                pending_msg="Cancelled by operator when the testing episode was cancelled.",
                predelivery_msg="Cancelled by operator before delivery to a runner.",
            )
            for task in store.list(
                Task,
                workspace_id=ctx.workspace_id,
                project_id=episode.project_id,
                run_id=episode.id,
            )
        )

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
        answered = clarifications.apply_answer(
            store, supervisor, config, project, question, body.answer
        )
        return answered.model_dump()

    @app.post("/api/questions/{question_id}/dismiss")
    def dismiss_question(question_id: str, ctx: AuthContext = Depends(current)):
        question = store.get(Question, question_id)
        if not question or question.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        return clarifications.dismiss(store, supervisor, question).model_dump()

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
        was_pending = task.status == TaskStatus.pending
        cancel_one_task(
            task,
            pending_msg="Cancelled by operator before dispatch.",
            predelivery_msg="Cancelled by operator before delivery to a runner.",
        )
        if was_pending:
            supervisor.wake(task.project_id, f"Task {task.id} was cancelled before it ran.")
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
        # is the same grouping the home dashboard uses (see hive._control.capacity).
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
        task, resource = registration.queue_probe(store, resource, runner)
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
    def list_human_todos(ctx: AuthContext = Depends(current)):
        return [t.model_dump() for t in store.list(HumanTask, workspace_id=ctx.workspace_id)]

    @app.post("/api/human-todos")
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
        return registration.register(store, body, workspace_id)

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
    from hive._control.orchestrator import Orchestrator

    orchestrator = Orchestrator(store, blobs, config)

    def ci_check(project_id: str) -> None:
        """Supervisor callback: poll each repo's CI for a ci_autofix project and
        file+queue a fix when red. Needs a GitHub token; a no-op without one."""
        project = store.get(Project, project_id)
        if not project or not project.ci_autofix or not config.gh_token.strip():
            return
        from hive._workstreams.ci import check_and_autofix
        from hive._workstreams.issues import ensure_issue_workstream

        repos = dict.fromkeys(
            r.strip() for r in [project.spec_repo, *project.member_repos] if r.strip()
        )
        for repo in repos:
            try:
                workstream = ensure_issue_workstream(store, project, repo=repo)
                if not workstream.enabled:
                    continue
                check_and_autofix(
                    store,
                    project,
                    workstream,
                    config.gh_token,
                    issue_backend=config.issue_backend,
                    issue_model=config.issue_model,
                )
            except Exception:
                logging.getLogger("hive.api").exception(
                    "CI auto-check failed for project %s repo %s", project_id, repo
                )

    supervisor = Supervisor(
        store,
        orchestrator.invoke,
        workspace_id=config.workspace_id,
        machine_name=config.machine_name,
        ci_check=ci_check,
    )
    from hive.runner._local import LocalRunnerManager

    app = create_app(
        store,
        supervisor,
        config,
        blobs=blobs,
        local_runner=LocalRunnerManager(config),
    )

    mount_spa(app, Path(os.environ.get("HIVE_WEB_DIST", "web/dist")))
    return app
