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

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from hive.agent_probe import probe_instructions
from hive.config import Config
from hive.models import (
    Autonomy,
    Feedback,
    GuessPropensity,
    HumanTask,
    HumanTaskStatus,
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
    spec_repo: str
    member_repos: list[str] = []
    mission: str = ""
    iteration_goal: str = ""
    mode: Mode = Mode.build
    autonomy: Autonomy = Autonomy.direct_push
    guess_propensity: GuessPropensity = GuessPropensity.sometimes


class ProjectPatch(BaseModel):
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


class RunnerRegister(BaseModel):
    name: str
    backends: list[str]
    boot: bool = False  # true on daemon startup (vs periodic heartbeat)


class TaskResult(BaseModel):
    text: str
    is_error: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    resource_exhausted: bool = False  # rate limit / quota detected by runner
    cancelled: bool = False  # runner stopped the task on an operator cancel request


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


def create_app(store, supervisor: Supervisor, config: Config, blobs=None) -> FastAPI:
    app = FastAPI(title="hive")

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
        title = f"Repair spec logging for {project.name}"
        already_open = any(
            task.status == HumanTaskStatus.open
            and task.project_id == project.id
            and task.title == title
            for task in store.list(HumanTask)
        )
        if already_open:
            return
        store.put(
            HumanTask(
                project_id=project.id,
                title=title,
                instructions=(
                    "Hive saved a clarification answer in the control-plane DB, but could not "
                    "append the raw answer to the spec repo input log.\n\n"
                    f"Question: `{question.id}`\n\n"
                    f"Spec repo: `{project.spec_repo}`\n\n"
                    f"Error:\n\n```\n{type(exc).__name__}: {str(exc)[:1500]}\n```\n\n"
                    "Fix spec-repo write access, then ask Hive to distill or replay the answer "
                    "from the project question history."
                ),
            )
        )

    def runner_auth(x_hive_token: str = Header(default="")) -> None:
        if x_hive_token != config.runner_token:
            raise HTTPException(401, "bad runner token")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        supervisor.acquire_leadership()  # raises if another control plane is live
        loop_task = asyncio.create_task(supervisor.run_forever())
        yield
        loop_task.cancel()

    app.router.lifespan_context = lifespan
    app.state.supervisor = supervisor
    app.state.store = store

    # ---- web API -------------------------------------------------------------

    @app.get("/api/projects")
    def list_projects():
        return [p.model_dump() for p in store.list(Project)]

    @app.post("/api/projects")
    def create_project(body: ProjectCreate):
        project = store.put(Project(**body.model_dump(exclude={"mission", "iteration_goal"})))
        mission = body.mission.strip()
        iteration_goal = body.iteration_goal.strip()
        if mission or iteration_goal:
            event = (
                "Project created with an initial brief from the user.\n\n"
                f"Mission:\n{mission or '(not provided)'}\n\n"
                f"Initial iteration goal:\n{iteration_goal or '(not provided)'}\n\n"
                "Your FIRST action must be commit_to_spec: write the mission to mission.md "
                "and the iteration goal to iteration.md, preserving any existing useful spec "
                "context. Only then plan the opening workstreams."
            )
        else:
            event = "Project created. Plan the opening workstreams."
        supervisor.wake(project.id, event)
        return project.model_dump()

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        project = store.get(Project, project_id)
        if not project:
            raise HTTPException(404)
        return {
            "project": project.model_dump(),
            "workstreams": [w.model_dump() for w in store.list(Workstream, project_id=project_id)],
            "tasks": [t.model_dump() for t in store.list(Task, project_id=project_id)[-50:]],
            "questions": [q.model_dump() for q in store.list(Question, project_id=project_id)],
            "human_tasks": [t.model_dump() for t in store.list(HumanTask, project_id=project_id)],
        }

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: ProjectPatch):
        project = store.get(Project, project_id)
        if not project:
            raise HTTPException(404)
        updates = body.model_dump(exclude_none=True)
        note = updates.pop("new_iteration_note", None)
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
    def answer_question(question_id: str, body: AnswerBody):
        question = store.get(Question, question_id)
        if not question:
            raise HTTPException(404)
        project = store.get(Project, question.project_id)
        if not project:
            raise HTTPException(404, "question project not found")
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
    def dismiss_question(question_id: str):
        question = store.get(Question, question_id)
        if not question:
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
    def add_feedback(body: FeedbackBody):
        return store.put(Feedback(**body.model_dump())).model_dump()

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        task = store.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        return task.model_dump()

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        task = store.get(Task, task_id)
        if not task:
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

    @app.post("/api/tasks/{task_id}/trace", dependencies=[Depends(runner_auth)])
    async def upload_trace(task_id: str, request: Request):
        task = store.get(Task, task_id)
        if not task:
            raise HTTPException(404)
        if blobs is None:
            raise HTTPException(503, "no blob store configured")
        key = f"traces/{task_id}.jsonl"
        blobs.put(key, await request.body())
        task.trace_blob = key
        store.put(task)
        return {"ok": True}

    @app.get("/api/tasks/{task_id}/trace")
    def get_trace(task_id: str):
        task = store.get(Task, task_id)
        if not task or not task.trace_blob or blobs is None:
            raise HTTPException(404)
        data = blobs.get(task.trace_blob)
        if data is None:
            raise HTTPException(404)
        return PlainTextResponse(data, media_type="application/x-ndjson")

    @app.get("/api/resources")
    def resources():
        return {
            "runners": [
                {**r.model_dump(), "online": r.online()} for r in store.list(Runner)
            ],
            "resources": [
                {**r.model_dump(), "available": r.available()} for r in store.list(Resource)
            ],
        }

    @app.post("/api/resources/{resource_id}/probe")
    def probe_resource(resource_id: str):
        resource = store.get(Resource, resource_id)
        if not resource:
            raise HTTPException(404)
        runner = store.get(Runner, resource.runner_id)
        if not runner or not runner.online():
            raise HTTPException(409, "runner is offline")
        if resource.backend not in runner.backends:
            raise HTTPException(409, "runner no longer advertises this backend")

        repo = _ensure_probe_repo(Path(config.data_dir or "/tmp/hive-data"))
        task = store.put(
            Task(
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
        return {"task": task.model_dump(), "resource": {**resource.model_dump(), "available": resource.available()}}

    @app.get("/api/subscriptions")
    def list_subscriptions():
        return [s.model_dump() for s in store.list(Subscription)]

    @app.post("/api/subscriptions")
    def create_subscription(body: dict):
        return store.put(
            Subscription(
                provider=body["provider"],
                plan=body.get("plan", ""),
                notes=body.get("notes", ""),
            )
        ).model_dump()

    @app.delete("/api/subscriptions/{sub_id}")
    def delete_subscription(sub_id: str):
        store.delete(Subscription, sub_id)
        return {"ok": True}

    @app.get("/api/human-tasks")
    def list_human_tasks():
        return [t.model_dump() for t in store.list(HumanTask)]

    @app.post("/api/human-tasks")
    def create_human_task(body: dict):
        return store.put(
            HumanTask(
                project_id=body.get("project_id", ""),
                title=body["title"],
                instructions=body.get("instructions", ""),
            )
        ).model_dump()

    @app.post("/api/human-tasks/{task_id}/done")
    def complete_human_task(task_id: str):
        task = store.get(HumanTask, task_id)
        if not task:
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
            for project in store.list(Project):
                supervisor.wake(project.id, note)
        return task.model_dump()

    @app.get("/api/org-context")
    def get_org_context():
        return {"text": store.get_org_context()}

    @app.put("/api/org-context")
    def set_org_context(body: dict):
        store.set_org_context(body.get("text", ""))
        return {"ok": True}

    # ---- runner protocol -------------------------------------------------------

    @app.post("/api/runners/register", dependencies=[Depends(runner_auth)])
    def register_runner(body: RunnerRegister):
        existing = next((r for r in store.list(Runner) if r.name == body.name), None)
        runner = existing or Runner(name=body.name)
        runner.backends = body.backends
        runner.last_seen = time.time()
        store.put(runner)
        present = {(r.runner_id, r.backend) for r in store.list(Resource)}
        for backend in body.backends:
            if (runner.id, backend) not in present:
                store.put(Resource(runner_id=runner.id, backend=backend))
        if body.boot:
            # A booting daemon executes nothing: whatever was in flight on this
            # runner died with the previous process — requeue it.
            for task in store.list(Task, status=TaskStatus.running, runner_id=runner.id):
                task.status = TaskStatus.pending
                task.runner_id = ""
                task.delivered = False
                store.put(task)
                log.info("requeued task %s after runner %s reboot", task.id, runner.name)
        return {"runner_id": runner.id}

    @app.post("/api/runners/{runner_id}/poll", dependencies=[Depends(runner_auth)])
    async def poll(runner_id: str):
        runner = store.get(Runner, runner_id)
        if not runner:
            raise HTTPException(404, "unknown runner — re-register")
        deadline = time.monotonic() + RUNNER_POLL_WAIT_S
        while True:
            runner.last_seen = time.time()
            store.put(runner)
            for task in store.list(Task, status=TaskStatus.running, runner_id=runner_id):
                if not task.delivered:
                    task.delivered = True
                    store.put(task)
                    return {"task": task.model_dump()}
            if time.monotonic() > deadline:
                return {"task": None}
            await asyncio.sleep(2.0)

    @app.post("/api/tasks/{task_id}/result", dependencies=[Depends(runner_auth)])
    def task_result(task_id: str, body: TaskResult):
        task = store.get(Task, task_id)
        if not task:
            raise HTTPException(404)
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
        store.put(task)

        for resource in store.list(Resource, runner_id=task.runner_id, backend=task.backend):
            resource.total_tasks += 1
            resource.total_cost_usd += body.cost_usd
            if task.kind == TaskKind.probe and resource.last_probe_task_id == task.id:
                resource.last_probe_at = task.finished_at
                resource.last_probe_text = body.text[:2000]
                if body.cancelled:
                    resource.usability_status = ResourceUsability.unknown
                elif body.is_error:
                    resource.usability_status = ResourceUsability.failed
                else:
                    resource.usability_status = ResourceUsability.usable
            if body.resource_exhausted:
                resource.cooldown_until = time.time() + RATE_LIMIT_COOLDOWN_S
            store.put(resource)

        if task.kind == TaskKind.probe:
            if body.is_error and HUMAN_FIX_PATTERNS.search(body.text):
                runner = store.get(Runner, task.runner_id)
                title = f"Fix {task.backend} login on {runner.name if runner else task.runner_id}"
                already_open = any(
                    t.status == HumanTaskStatus.open and t.title == title
                    for t in store.list(HumanTask)
                )
                if not already_open:
                    store.put(
                        HumanTask(
                            title=title,
                            instructions=(
                                f"Refresh or repair the `{task.backend}` CLI login on runner "
                                f"`{runner.name if runner else task.runner_id}`, then rerun the resource probe.\n\n"
                                f"Recent probe output:\n\n```\n{body.text[:1500]}\n```"
                            ),
                        )
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
    if config.gcp_project:
        from hive.store import FirestoreStore

        store = FirestoreStore(config.gcp_project)
    else:
        from hive.store import MemoryStore

        store = MemoryStore()
    if config.gcs_bucket:
        from hive.blobstore import GcsBlobStore

        blobs = GcsBlobStore(config.gcs_bucket)
    else:
        from hive.blobstore import LocalBlobStore

        blobs = LocalBlobStore(config.data_dir / "blobs")
    from hive.orchestrator import Orchestrator

    orchestrator = Orchestrator(store, blobs, config)
    supervisor = Supervisor(store, orchestrator.invoke)
    app = create_app(store, supervisor, config, blobs=blobs)

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
