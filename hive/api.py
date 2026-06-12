"""FastAPI control plane: web API + runner protocol + app wiring.

Build with `create_app()` for production (env config) or pass explicit pieces
in tests. Web endpoints are unauthenticated (the service sits behind
Tailscale); runner endpoints require the shared runner token.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

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
    Runner,
    Subscription,
    Task,
    TaskStatus,
    Workstream,
)
from hive.supervisor import Supervisor

log = logging.getLogger("hive.api")

RUNNER_POLL_WAIT_S = 25.0
RATE_LIMIT_COOLDOWN_S = 3600.0


class ProjectCreate(BaseModel):
    name: str
    spec_repo: str
    member_repos: list[str] = []
    mode: Mode = Mode.build
    autonomy: Autonomy = Autonomy.direct_push
    guess_propensity: GuessPropensity = GuessPropensity.sometimes


class ProjectPatch(BaseModel):
    mode: Mode | None = None
    autonomy: Autonomy | None = None
    guess_propensity: GuessPropensity | None = None
    prod_deploys: bool | None = None
    paused: bool | None = None
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


def create_app(store, supervisor: Supervisor, config: Config) -> FastAPI:
    app = FastAPI(title="hive")

    def runner_auth(x_hive_token: str = Header(default="")) -> None:
        if x_hive_token != config.runner_token:
            raise HTTPException(401, "bad runner token")

    @contextlib.asynccontextmanager
    async def lifespan(_app):
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
        project = store.put(Project(**body.model_dump()))
        supervisor.wake(project.id, "Project created. Plan the opening workstreams.")
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
            supervisor.wake(project_id, f"New iteration started by user: {note}")
        store.put(project)
        return project.model_dump()

    @app.post("/api/questions/{question_id}/answer")
    def answer_question(question_id: str, body: AnswerBody):
        question = store.get(Question, question_id)
        if not question:
            raise HTTPException(404)
        question.status = QuestionStatus.answered
        question.answer = body.answer
        question.answered_at = time.time()
        store.put(question)
        supervisor.wake(
            question.project_id,
            f"User answered question {question.id}.\nQ: {question.text}\nA: {body.answer}\n"
            "Distill this into the spec repo and continue.",
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
        return store.put(task).model_dump()

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
        task.status = TaskStatus.failed if body.is_error else TaskStatus.done
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
            if body.resource_exhausted:
                resource.cooldown_until = time.time() + RATE_LIMIT_COOLDOWN_S
            store.put(resource)

        outcome = "failed" if body.is_error else "finished"
        supervisor.wake(
            task.project_id,
            f"{task.kind} task {task.id} (ws {task.workstream_id}, repo {task.repo}) {outcome}.\n"
            f"Result:\n{body.text[:6000]}",
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
    app = create_app(store, supervisor, config)

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
