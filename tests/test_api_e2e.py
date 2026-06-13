"""End-to-end mocked run through the HTTP API.

A scripted orchestrator (no LLM) plays the planning role; a fake runner uses
the real runner protocol endpoints. Verifies the full loop: project creation →
workstream/task planning → dispatch → runner poll → result → verify task →
question → answer → goal complete.
"""

import time

import pytest
from fastapi.testclient import TestClient

from hive.agent_probe import PROBE_MARKER
from hive.blobstore import LocalBlobStore
from hive.config import Config
from hive.models import HumanTask, Project, Question, Resource, Task, TaskKind, TaskStatus, Workstream
from hive.orchestrator import Tools
from hive.store import MemoryStore
from hive.supervisor import Supervisor

RUNNER_HEADERS = {"X-Hive-Token": "test-token"}


class ScriptedOrchestrator:
    """Plays the orchestrator: plans one workstream/task, verifies after work,
    asks a question after verify, completes the goal after the answer."""

    def __init__(self, store):
        self.store = store
        self.invocations: list[list[str]] = []

    def invoke(self, project_id: str, events: list[str]) -> None:
        self.invocations.append(events)
        project = self.store.get(Project, project_id)
        tools = Tools(self.store, project, spec=None)
        tasks = self.store.list(Task, project_id=project_id)
        questions = self.store.list(Question, project_id=project_id)

        if not self.store.list(Workstream, project_id=project_id):
            ws_id = tools.create_workstream("build", "build the thing").split("=")[1]
            tools.create_task(ws_id, "https://example.com/app.git", "implement feature")
        elif any("answered question" in e for e in events):
            tools.mark_goal_complete("done after clarification")
        elif any(t.kind == TaskKind.work and t.status == "done" for t in tasks) and not any(
            t.kind == TaskKind.verify for t in tasks
        ):
            ws_id = tasks[0].workstream_id
            tools.create_task(ws_id, "https://example.com/app.git", "verify it", kind="verify")
        elif any(t.kind == TaskKind.verify and t.status == "done" for t in tasks) and not questions:
            tools.ask_user("Should we also add B? My recommendation: yes.", tasks[0].workstream_id)


@pytest.fixture
def harness(tmp_path):
    store = MemoryStore()
    orch = ScriptedOrchestrator(store)
    supervisor = Supervisor(store, orch.invoke)
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    from hive.api import create_app

    app = create_app(store, supervisor, config, blobs=LocalBlobStore(tmp_path / "blobs"))
    # No context manager: lifespan (the background loop) stays off; tests pump manually.
    yield TestClient(app), store, orch


def test_full_loop(harness):
    client, store, orch = harness

    # 1. create project → orchestrator plans a workstream + task
    project = client.post(
        "/api/projects",
        json={"name": "demo", "spec_repo": "https://example.com/spec.git"},
    ).json()
    pid = project["id"]
    _pump(client, store)
    detail = client.get(f"/api/projects/{pid}").json()
    assert len(detail["workstreams"]) == 1
    assert len(detail["tasks"]) == 1

    # 2. runner registers and polls — gets the task after dispatch
    rid = _register_usable_runner(client)
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task is not None and task["kind"] == "work"

    # 3. work result → orchestrator queues a verify task
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "implemented, tests pass", "cost_usd": 0.5},
        headers=RUNNER_HEADERS,
    )
    _pump(client, store)
    verify = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert verify is not None and verify["kind"] == "verify"
    assert "VERDICT" in verify["instructions"]

    # 4. verify result → orchestrator asks a question, workstream parks
    client.post(
        f"/api/tasks/{verify['id']}/result",
        json={"text": "VERDICT: ACCEPT"},
        headers=RUNNER_HEADERS,
    )
    _pump(client, store)
    assert store.get(Task, verify["id"]).verdict == "accept"  # parsed deterministically
    detail = client.get(f"/api/projects/{pid}").json()
    assert len(detail["questions"]) == 1
    assert detail["workstreams"][0]["status"] == "parked"

    # 5. answer → goal complete; resource usage was recorded
    qid = detail["questions"][0]["id"]
    client.post(f"/api/questions/{qid}/answer", json={"answer": "yes, add B"})
    _pump(client, store)
    project = client.get(f"/api/projects/{pid}").json()["project"]
    assert project["goal_complete"]
    assert project["state"] == "idle_goal_complete"

    resources = client.get("/api/resources").json()
    assert resources["resources"][0]["total_tasks"] == 3
    assert resources["resources"][0]["total_cost_usd"] == 0.5


def test_human_task_tool_and_api(harness):
    client, store, _orch = harness
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    tools = Tools(store, project, spec=None)
    out = tools.create_human_task("Log in codex on vm-1", "run `codex login`", org_wide=True)
    task_id = out.split("=")[1].split()[0]

    assert "Log in codex on vm-1" in tools.snapshot()
    assert client.get("/api/human-tasks").json()[0]["status"] == "open"

    # Another project's scoped todo is invisible here; org-wide ones are shared.
    other = store.put(Project(name="other", spec_repo="https://example.com/o.git"))
    Tools(store, other, spec=None).create_human_task("Grant repo access", "add bot to o.git")
    assert "Grant repo access" not in tools.snapshot()
    assert "Grant repo access" in Tools(store, other, spec=None).snapshot()

    assert client.post(f"/api/human-tasks/{task_id}/done").json()["status"] == "done"
    assert "Log in codex" not in tools.snapshot()  # only open todos are shown


def test_rate_limited_result_sets_cooldown(harness):
    client, store, orch = harness
    client.post("/api/projects", json={"name": "p2", "spec_repo": "https://example.com/s.git"})
    _pump(client, store)
    rid = _register_usable_runner(client, name="r2")
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "429 rate limit", "is_error": True, "resource_exhausted": True},
        headers=RUNNER_HEADERS,
    )
    res = client.get("/api/resources").json()["resources"][0]
    assert not res["available"]
    assert res["cooldown_until"] > time.time()
    assert res["usability_status"] == "usable"


def test_runner_auth_required(harness):
    client, *_ = harness
    assert client.post("/api/runners/register", json={"name": "x", "backends": []}).status_code == 401


def test_resource_probe_marks_usable_and_failed(harness):
    client, store, _orch = harness
    rid = client.post(
        "/api/runners/register",
        json={"name": "probe-runner", "backends": ["cursor"]},
        headers=RUNNER_HEADERS,
    ).json()["runner_id"]
    resource = client.get("/api/resources").json()["resources"][0]
    assert resource["usability_status"] == "unknown"
    assert not resource["available"]

    queued = client.post(f"/api/resources/{resource['id']}/probe").json()
    assert queued["task"]["kind"] == "probe"
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task["id"] == queued["task"]["id"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": PROBE_MARKER},
        headers=RUNNER_HEADERS,
    )
    resource = store.get(Resource, resource["id"])
    assert resource.usability_status == "usable"
    assert resource.available()

    queued = client.post(f"/api/resources/{resource.id}/probe").json()
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "codex login required", "is_error": True},
        headers=RUNNER_HEADERS,
    )
    resource = store.get(Resource, resource.id)
    assert resource.usability_status == "failed"
    assert not resource.available()
    assert store.list(HumanTask)[0].title == "Fix cursor login on probe-runner"


def test_cancel_pending_task(harness):
    client, store, _orch = harness
    client.post("/api/projects", json={"name": "c", "spec_repo": "https://example.com/s.git"})
    _pump(client, store)  # orchestrator queues a work task; no runner online → stays pending
    task = store.list(Task)[0]
    assert task.status == "pending"
    assert client.post(f"/api/tasks/{task.id}/cancel").json()["status"] == "cancelled"


def test_dismiss_question_wakes(harness):
    client, store, _orch = harness
    pid = client.post(
        "/api/projects", json={"name": "d", "spec_repo": "https://example.com/s.git"}
    ).json()["id"]
    sup = client.app.state.supervisor
    q = store.put(Question(project_id=pid, text="pick A or B?"))
    sup._events.clear()
    assert client.post(f"/api/questions/{q.id}/dismiss").json()["status"] == "dismissed"
    assert sup._events.get(pid)  # orchestrator is woken to reconsider the parked workstream


def test_trace_roundtrip(harness):
    client, store, _orch = harness
    pid = client.post(
        "/api/projects", json={"name": "t", "spec_repo": "https://example.com/s.git"}
    ).json()["id"]
    ws = store.put(Workstream(project_id=pid, title="w"))
    task = store.put(Task(project_id=pid, workstream_id=ws.id, repo="r", instructions="i",
                          status=TaskStatus.running))
    trace = b'{"event":"run_init"}\n{"event":"agent_run_end","cost_usd":0.1}\n'
    assert client.post(
        f"/api/tasks/{task.id}/trace", content=trace, headers=RUNNER_HEADERS
    ).json()["ok"]
    assert store.get(Task, task.id).trace_blob == f"traces/{task.id}.jsonl"
    got = client.get(f"/api/tasks/{task.id}/trace")
    assert got.status_code == 200 and b"run_init" in got.content
    # Trace upload is a runner action — unauthenticated callers are rejected.
    assert client.post(f"/api/tasks/{task.id}/trace", content=trace).status_code == 401


def test_human_task_done_wakes_project(harness):
    client, store, _orch = harness
    pid = client.post(
        "/api/projects", json={"name": "h", "spec_repo": "https://example.com/s.git"}
    ).json()["id"]
    task = client.post("/api/human-tasks", json={"title": "login", "project_id": pid}).json()
    sup = client.app.state.supervisor
    sup._events.clear()
    client.post(f"/api/human-tasks/{task['id']}/done")
    assert sup._events.get(pid)  # completing the action re-evaluates work that waited on it


def _pump(client, store, rounds: int = 4):
    """Run supervisor steps synchronously: dispatch + drain orchestrator wakes.

    The production supervisor loop is asyncio-driven; tests pump it manually
    for determinism.
    """
    app_supervisor = client.app.state.supervisor
    for _ in range(rounds):
        app_supervisor.fail_orphaned_tasks()
        for project in store.list(Project):
            app_supervisor.dispatch(project)
            app_supervisor.refresh_state(project)
            events = app_supervisor._events.pop(project.id, [])
            if events:
                app_supervisor.orchestrate(project.id, events)
        for project in store.list(Project):
            app_supervisor.dispatch(project)
            app_supervisor.refresh_state(project)


def _register_usable_runner(client, name: str = "fake-runner", backend: str = "cursor") -> str:
    rid = client.post(
        "/api/runners/register",
        json={"name": name, "backends": [backend]},
        headers=RUNNER_HEADERS,
    ).json()["runner_id"]
    resource = client.get("/api/resources").json()["resources"][-1]
    queued = client.post(f"/api/resources/{resource['id']}/probe").json()
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task["id"] == queued["task"]["id"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": PROBE_MARKER},
        headers=RUNNER_HEADERS,
    )
    return rid
