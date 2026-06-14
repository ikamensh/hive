"""FastAPI control plane: web API + runner protocol + app wiring.

Build with `create_app()` for production (env config) or pass explicit pieces
in tests. Web endpoints are unauthenticated (the service sits behind
Tailscale); runner endpoints require the shared runner token.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import re
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from hive.auth import (
    SESSION_COOKIE,
    SESSION_TTL_S,
    AuthContext,
    AuthManager,
    ensure_control_plane_machine,
    ensure_machine,
)
from hive.backends import REGISTRY, probe_instructions
from hive.config import Config
from hive.escalation import escalate
from hive.github_repos import all_repos as list_github_repos
from hive.models import (
    Autonomy,
    Feedback,
    GuessPropensity,
    HumanTask,
    HumanTaskStatus,
    Machine,
    Mode,
    Project,
    Question,
    QuestionStatus,
    Resource,
    ResourceUsability,
    Runner,
    Subscription,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
    parse_verdict,
)
from hive.specrepo import SpecRepo
from hive.storage import export_to_gcp, storage_info
from hive.store import FileStore
from hive.supervisor import Supervisor

log = logging.getLogger("hive.api")

RUNNER_POLL_WAIT_S = 25.0
RATE_LIMIT_COOLDOWN_S = 3600.0
PROBE_REPO_DIR = "agent-probe-repo"
HUMAN_FIX_PATTERNS = re.compile(
    r"auth|login|credential|api.?key|not authenticated|forbidden|permission|subscription|billing",
    re.IGNORECASE,
)


def _iso_utc(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).isoformat()


class ProjectCreate(BaseModel):
    name: str


class ProjectStart(BaseModel):
    mission: str = ""
    iteration_goal: str = ""


class ProjectPatch(BaseModel):
    spec_repo: str | None = None
    mode: Mode | None = None
    autonomy: Autonomy | None = None
    guess_propensity: GuessPropensity | None = None
    prod_deploys: bool | None = None
    paused: bool | None = None
    daily_budget_usd: float | None = None
    member_repos: list[str] | None = None
    new_iteration_note: str | None = None  # set when starting the next iteration


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
    auto_probe: bool = False


class TaskResult(BaseModel):
    text: str
    is_error: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    resource_exhausted: bool = False  # rate limit / quota detected by runner
    cancelled: bool = False  # runner stopped the task on an operator cancel request


class ResourcePatch(BaseModel):
    enabled: bool | None = None
    disabled_reason: str = ""


class LocalRunnerPatch(BaseModel):
    autostart: bool


class StorageExport(BaseModel):
    gcp_project: str
    gcs_bucket: str = ""


def _ensure_probe_repo(data_dir: Path) -> Path:
    repo = data_dir / PROBE_REPO_DIR
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, timeout=60)
        subprocess.run(["git", "config", "user.email", "hive-probe@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Hive Probe"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Hive agent probe\n\nThis repository is only for backend usability checks.\n")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, timeout=60)
        subprocess.run(["git", "commit", "-m", "Initial probe repo"], cwd=repo, check=True, timeout=60)
    return repo


def create_app(store, supervisor: Supervisor, config: Config, blobs=None, local_runner=None) -> FastAPI:
    app = FastAPI(title="hive")
    auth = AuthManager(store, config)
    auth.validate_config()
    control_machine = ensure_control_plane_machine(store, config)

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

    def can_write_spec_repo(project: Project) -> bool:
        """Avoid slow surprise network attempts in throwaway/local runs.

        Production has HIVE_GH_TOKEN; tests and local smoke runs often use a
        filesystem path. Other remotes can still be handled by the orchestrator
        via commit_to_spec, but the control plane only auto-writes when it has
        an obvious write path.
        """
        url = project.spec_repo
        return bool(config.gh_token.strip()) or url.startswith("file://") or Path(url).exists()

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
                "Hive saved a clarification answer in the control-plane DB, but could not "
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
        ensure_control_plane_machine(store, config)
        supervisor.acquire_leadership()  # raises if another control plane is live
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
            if local_runner is not None:
                local_runner.stop()
            loop_task.cancel()

    app.router.lifespan_context = lifespan
    app.state.supervisor = supervisor
    app.state.store = store
    app.state.auth = auth
    app.state.machine = control_machine

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

        repo = _ensure_probe_repo(Path(config.data_dir or "/tmp/hive-data"))
        task = store.put(
            Task(
                workspace_id=resource.workspace_id,
                project_id="",
                workstream_id="",
                repo=str(repo),
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

    def complete_resource_login_todos(resource: Resource) -> None:
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
        from hive.github_repos import parse_repo_ref, validate_repo

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

    @app.get("/api/projects")
    def list_projects(ctx: AuthContext = Depends(current)):
        return [p.model_dump() for p in store.list(Project, workspace_id=ctx.workspace_id)]

    def planning_wake_event(mission: str, iteration_goal: str) -> str:
        mission = mission.strip()
        iteration_goal = iteration_goal.strip()
        if mission or iteration_goal:
            return (
                "Project started with an initial brief from the user.\n\n"
                f"Mission:\n{mission or '(not provided)'}\n\n"
                f"Initial iteration goal:\n{iteration_goal or '(not provided)'}\n\n"
                "Your FIRST action must be commit_to_spec: write the mission to mission.md "
                "and the iteration goal to iteration.md, preserving any existing useful spec "
                "context. Only then plan the opening workstreams."
            )
        return "Project configured. Plan the opening workstreams."

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
        supervisor.wake(project_id, planning_wake_event(body.mission, body.iteration_goal))
        return project.model_dump()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, ctx: AuthContext = Depends(current)):
        project = require_project(project_id, ctx)
        return {
            "project": project.model_dump(),
            "workstreams": [
                w.model_dump()
                for w in store.list(Workstream, workspace_id=ctx.workspace_id, project_id=project_id)
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
            "human_tasks": [
                t.model_dump()
                for t in store.list(HumanTask, workspace_id=ctx.workspace_id, project_id=project_id)
            ],
            "spend_today": supervisor.spend_today(project_id),
        }

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch, ctx: AuthContext = Depends(current)):
        project = require_project(project_id, ctx)
        updates = body.model_dump(exclude_none=True)
        note = updates.pop("new_iteration_note", None)
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
                    "Control plane already appended the raw answer to "
                    f"{record_answer_input_log(project, question, body.answer, answered_at)}.\n"
                )
            except Exception as exc:
                log.warning("failed to append question %s to spec input-log: %s", question.id, exc)
                file_spec_log_failure(project, question, exc)
                input_log_note = (
                    "Control plane could not append the raw answer to input-log automatically; "
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
            supervisor.wake(task.project_id, f"Task {task.id} was cancelled before it ran.")
        elif task.status == TaskStatus.running:
            # Cooperative: the runner polls this flag and stops the agent.
            task.cancel_requested = True
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
        runners = {r.id: r for r in store.list(Runner, workspace_id=ctx.workspace_id)}

        def resource_payload(resource: Resource) -> dict:
            runner = runners.get(resource.runner_id)
            available = (
                resource.available()
                and runner is not None
                and runner.online()
                and resource.backend in runner.backends
            )
            return {**resource.model_dump(), "available": available}

        return {
            "machines": [
                m.model_dump() for m in store.list(Machine, workspace_id=ctx.workspace_id)
            ],
            "runners": [
                {**r.model_dump(), "online": r.online()} for r in runners.values()
            ],
            "resources": [
                resource_payload(r)
                for r in store.list(Resource, workspace_id=ctx.workspace_id)
            ],
            "local_runner": local_runner_payload(ctx.workspace_id),
        }

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
            complete_resource_login_todos(updated)
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

    @app.get("/api/human-tasks")
    def list_human_tasks(ctx: AuthContext = Depends(current)):
        return [t.model_dump() for t in store.list(HumanTask, workspace_id=ctx.workspace_id)]

    @app.post("/api/human-tasks")
    def create_human_task(body: dict, ctx: AuthContext = Depends(current)):
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

    @app.post("/api/human-tasks/{task_id}/done")
    def complete_human_task(task_id: str, ctx: AuthContext = Depends(current)):
        task = store.get(HumanTask, task_id)
        if not task or task.workspace_id != ctx.workspace_id:
            raise HTTPException(404)
        task.status = HumanTaskStatus.done
        task.done_at = time.time()
        store.put(task)
        # The action may have unblocked work (a runner login, a granted access).
        # Wake the owning project, or every project for an org-wide todo.
        note = f"Human task '{task.title}' was completed. Re-evaluate work that waited on it."
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

    @app.post("/api/storage/export")
    def export_storage(body: StorageExport, ctx: AuthContext = Depends(current)):
        if not isinstance(store, FileStore):
            raise HTTPException(409, "export is only available from the local file store")
        if not body.gcp_project.strip():
            raise HTTPException(400, "gcp_project is required")
        from hive.blobstore import LocalBlobStore

        if blobs is None or not isinstance(blobs, LocalBlobStore):
            raise HTTPException(409, "blob export requires a local blob store")
        try:
            return export_to_gcp(
                store,
                blobs,
                gcp_project=body.gcp_project.strip(),
                gcs_bucket=body.gcs_bucket.strip(),
            )
        except Exception as exc:
            log.exception("storage export failed")
            raise HTTPException(503, f"export failed: {exc}") from exc

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
            store.put(resource)
            resources_by_pair[(machine.id, backend)] = resource
            if body.auto_probe and resource.enabled and resource.usability_status == ResourceUsability.unknown:
                queue_probe(resource, runner)

        for discovery in body.discoveries:
            if discovery.installed:
                continue
            resource = resources_by_pair.get((machine.id, discovery.name))
            if resource:
                apply_discovery(resource, discovery)
                store.put(resource)

        return {"runner_id": runner.id, "machine_id": machine.id}

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
            await asyncio.sleep(2.0)

    @app.post("/api/tasks/{task_id}/result")
    def task_result(
        task_id: str,
        body: TaskResult,
        workspace_id: str = Depends(runner_auth),
    ):
        existing = store.get(Task, task_id)
        if not existing or existing.workspace_id != workspace_id:
            raise HTTPException(404)

        def record(task: Task) -> None:
            if body.cancelled:
                task.status = TaskStatus.cancelled
            else:
                task.status = TaskStatus.failed if body.is_error else TaskStatus.done
            if task.kind == TaskKind.verify and not body.cancelled:
                task.verdict = parse_verdict(body.text)
            task.result_text = body.text
            task.is_error = body.is_error
            task.cost_usd = body.cost_usd
            task.input_tokens = body.input_tokens
            task.output_tokens = body.output_tokens
            task.finished_at = time.time()

        task = store.update(Task, task_id, record)
        if task is None:
            raise HTTPException(404)

        probe_resource_enabled = True

        def account(resource: Resource) -> None:
            nonlocal probe_resource_enabled
            resource.total_tasks += 1
            resource.total_cost_usd += body.cost_usd
            if task.kind == TaskKind.probe and resource.last_probe_task_id == task.id:
                probe_resource_enabled = resource.enabled
                resource.last_probe_at = task.finished_at
                resource.last_probe_text = body.text[:2000]
                if body.cancelled:
                    resource.usability_status = ResourceUsability.unknown
                elif body.resource_exhausted:
                    resource.usability_status = ResourceUsability.usable
                elif body.is_error:
                    resource.usability_status = ResourceUsability.failed
                else:
                    resource.usability_status = ResourceUsability.usable
                    resource.clear_exhaustion()
            if body.resource_exhausted:
                resource.mark_exhausted(
                    until=time.time() + RATE_LIMIT_COOLDOWN_S,
                    at=task.finished_at,
                    text=body.text,
                    task_id=task.id,
                )

        for resource in store.list(
            Resource,
            workspace_id=workspace_id,
            runner_id=task.runner_id,
            backend=task.backend,
        ):
            store.update(Resource, resource.id, account)

        if task.kind == TaskKind.probe:
            if (
                probe_resource_enabled
                and body.is_error
                and not body.resource_exhausted
                and HUMAN_FIX_PATTERNS.search(body.text)
            ):
                runner = store.get(Runner, task.runner_id)
                runner_name = runner.name if runner else task.runner_id
                hint = REGISTRY.get(task.backend).login_hint if task.backend in REGISTRY else ""
                escalate(
                    store,
                    f"Fix {task.backend} login on {runner_name}",
                    instructions=(
                        f"Refresh or repair the `{task.backend}` CLI login on runner "
                        f"`{runner_name}`, then rerun the resource probe."
                        f"{chr(10) + chr(10) + hint if hint else ''}\n\n"
                        f"Recent probe output:\n\n```\n{body.text[:1500]}\n```"
                    ),
                    workspace_id=workspace_id,
                )
            return {"ok": True}

        outcome = "cancelled" if body.cancelled else ("failed" if body.is_error else "finished")
        verdict_note = (
            f" verdict={task.verdict}" if task.kind == TaskKind.verify and not body.cancelled else ""
        )
        supervisor.wake(
            task.project_id,
            f"{task.kind} task {task.id} (ws {task.workstream_id}, repo {task.repo}) "
            f"{outcome}{verdict_note}.\nResult:\n{body.text[:6000]}",
        )
        return {"ok": True}

    return app


def production_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = Config.from_env()
    from hive.storage import make_blob_store, make_store

    store = make_store(config)
    blobs = make_blob_store(config)
    from hive.orchestrator import Orchestrator

    machine = ensure_control_plane_machine(store, config)
    orchestrator = Orchestrator(store, blobs, config)
    supervisor = Supervisor(
        store,
        orchestrator.invoke,
        workspace_id=config.workspace_id,
        machine_name=machine.name,
    )
    from hive.local_runner import LocalRunnerManager

    app = create_app(
        store,
        supervisor,
        config,
        blobs=blobs,
        local_runner=LocalRunnerManager(config),
    )

    import os
    from pathlib import Path

    web_dir = Path(os.environ.get("HIVE_WEB_DIST", "web/dist"))
    if web_dir.is_dir():
        from fastapi.responses import FileResponse

        @app.get("/{path:path}")
        def spa(path: str):
            # Serve built assets; anything else falls back to the SPA shell so
            # deep links like /p/<id> work. /api routes are matched first.
            target = web_dir / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(web_dir / "index.html")

    return app
