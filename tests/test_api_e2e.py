"""End-to-end mocked run through the HTTP API.

A scripted orchestrator (no LLM) plays the planning role; a fake runner uses
the real runner protocol endpoints. Verifies the full loop: project creation →
workstream/task planning → dispatch → runner poll → result → verify task →
question → answer → goal complete.
"""

import time
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hive.runner._backends import PROBE_MARKER
from hive.persistence.blobstore import LocalBlobStore
from hive.config.settings import Config
from hive.llm._openai import OpenAIAdapter
from hive.models import (
    AgentConversation,
    ConversationStatus,
    HumanTask,
    HumanTaskStatus,
    OrchestratorRun,
    Project,
    Question,
    Resource,
    ResourceUsability,
    Runner,
    Subscription,
    Task,
    TaskKind,
    TaskStatus,
    Verdict,
    Workstream,
)
from hive._control.orchestrator import Orchestrator, Tools
from hive.persistence.store import MemoryStore
from hive._control.supervisor import Supervisor

RUNNER_HEADERS = {"X-Hive-Token": "test-token"}


def _configure_project(client, pid, spec_repo="https://example.com/spec.git", **patch):
    client.patch(f"/api/projects/{pid}", json={"spec_repo": spec_repo, **patch})


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _spec_origin(tmp_path, files: dict[str, str]):
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", "main", str(origin)], tmp_path)
    seed = tmp_path / "seed"
    _git(["clone", str(origin), str(seed)], tmp_path)
    for rel, content in files.items():
        target = seed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(["add", "-A"], seed)
    if files:
        _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "spec"], seed)
        _git(["push", "origin", "main"], seed)
    return origin


def _complete_intake(client, pid):
    store = client.app.state.store
    project = store.get(Project, pid)
    conversation = store.put(
        AgentConversation(
            workspace_id=project.workspace_id,
            project_id=pid,
            repo=project.spec_repo,
            backend="codex",
            model="gpt-5.5",
            status=ConversationStatus.done,
            latest_brief="Mission:\nBuild the thing.\n\nNext iteration:\nShip the first loop.",
        )
    )
    project.intake_conversation_id = conversation.id
    store.put(project)


def _start_project(client, pid, mission="", iteration_goal=""):
    _complete_intake(client, pid)
    client.post(f"/api/projects/{pid}/start", json={
        "mission": mission,
        "iteration_goal": iteration_goal,
    })


def _create_started(client, name, spec_repo="https://example.com/spec.git", mission="", iteration_goal=""):
    project = client.post("/api/projects", json={"name": name}).json()
    _configure_project(client, project["id"], spec_repo)
    _start_project(client, project["id"], mission, iteration_goal)
    return project


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
            work = next(t for t in tasks if t.kind == TaskKind.work and t.status == "done")
            tools.create_task(work.workstream_id, "https://example.com/app.git", "verify it", kind="verify")
        elif any(t.kind == TaskKind.verify and t.status == "done" for t in tasks) and not questions:
            work = next(t for t in tasks if t.kind == TaskKind.work)
            tools.ask_user(
                "## Include B in this iteration?\n\n"
                "The accepted verify covered A, but the spec leaves B adjacent to the same user journey.\n\n"
                "**Options:**\n\n"
                "1. Add B now while the code is warm.\n"
                "2. Ship A only and schedule B for a later iteration.\n\n"
                "**Recommendation:** add B now; it is cheap to include and avoids another partial pass.",
                work.workstream_id,
            )


class ScriptedOpenAIAdapter(OpenAIAdapter):
    """Real OpenAIAdapter with its HTTP scripted — exercises the live message
    plumbing (schemas, tool-result round-trip, model auto-select) sans network."""

    def __init__(self, *args, responses, models=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = list(responses)
        self.models = models or {"data": []}
        self.posts = []

    def _post(self, path: str, body: dict) -> dict:
        assert path == "/chat/completions"
        self.posts.append(body)
        return self.responses.pop(0)

    def _get(self, path: str) -> dict:
        assert path == "/models"
        return self.models


class AdapterOrchestrator(Orchestrator):
    """Orchestrator with the provider seam pinned to a supplied adapter."""

    def __init__(self, store, blobs, config, adapter):
        super().__init__(store, blobs, config)
        self.adapter = adapter

    def _build_adapters(self):
        return [self.adapter]


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


def test_spa_bundle_is_not_browser_cached(tmp_path):
    """`hive run` rebuilds web/dist, so reload/navigation must fetch the latest
    SPA shell and bundle instead of browser-cached old UI code."""
    web = tmp_path / "web"
    assets = web / "assets"
    assets.mkdir(parents=True)
    (web / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('fresh')", encoding="utf-8")
    from hive.api import mount_spa

    app = FastAPI()
    mount_spa(app, web)
    client = TestClient(app)

    for path in ("/", "/assets/app.js", "/p/project-id"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_lifespan_releases_leader_for_immediate_restart(tmp_path):
    store = MemoryStore()
    sup1 = Supervisor(store, ScriptedOrchestrator(store).invoke, machine_name="first")
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    from hive.api import create_app

    with TestClient(create_app(store, sup1, config)):
        pass

    sup2 = Supervisor(store, ScriptedOrchestrator(store).invoke, machine_name="second")
    sup2.acquire_leadership()


def test_full_loop(harness):
    client, store, orch = harness

    # 1. create + configure + start → orchestrator plans a workstream + task
    project = _create_started(client, "demo")
    pid = project["id"]
    _pump(client, store)
    detail = client.get(f"/api/projects/{pid}").json()
    iteration_stream = next(w for w in detail["workstreams"] if w["kind"] == "iteration")
    assert any(w["kind"] == "testing" for w in detail["workstreams"])
    assert len(detail["work_items"]) == 1
    assert detail["work_items"][0]["workstream_id"] == iteration_stream["id"]
    assert len(detail["tasks"]) == 1

    # 2. runner registers and polls — gets the task after dispatch
    rid = _register_usable_runner(client)
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task is not None and task["kind"] == "work"
    assert task["work_item_id"] == detail["work_items"][0]["id"]

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
    assert detail["work_items"][0]["status"] == "parked"

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


def test_create_draft_does_not_wake_orchestrator(harness):
    client, store, orch = harness
    project = client.post("/api/projects", json={"name": "draft"}).json()
    _pump(client, store)
    assert project["spec_repo"] == ""
    assert len(orch.invocations) == 0


def test_rename_and_archive_project(harness):
    """Renaming persists; blank names are rejected; archiving hides the project
    from the default list (data retained) but keeps it reachable directly and
    via include_archived. Regression for missing rename/delete UI."""
    client, _store, _orch = harness
    pid = client.post("/api/projects", json={"name": "old"}).json()["id"]

    assert client.patch(f"/api/projects/{pid}", json={"name": "  new  "}).json()["name"] == "new"
    assert client.patch(f"/api/projects/{pid}", json={"name": "   "}).status_code == 400

    client.patch(f"/api/projects/{pid}", json={"archived": True})
    listed = {p["id"] for p in client.get("/api/projects").json()}
    assert pid not in listed
    with_archived = {p["id"] for p in client.get("/api/projects?include_archived=true").json()}
    assert pid in with_archived
    assert client.get(f"/api/projects/{pid}").status_code == 200

    client.patch(f"/api/projects/{pid}", json={"archived": False})
    assert pid in {p["id"] for p in client.get("/api/projects").json()}


def test_start_requires_spec_repo(harness):
    client, store, orch = harness
    project = client.post("/api/projects", json={"name": "draft"}).json()
    assert client.post(f"/api/projects/{project['id']}/start", json={}).status_code == 400
    assert len(orch.invocations) == 0


def test_start_requires_completed_intake(harness):
    client, store, orch = harness
    project = client.post("/api/projects", json={"name": "draft"}).json()
    _configure_project(client, project["id"])
    assert client.post(f"/api/projects/{project['id']}/start", json={}).status_code == 409
    assert len(orch.invocations) == 0


def test_start_after_intake_wakes_orchestrator_and_ignores_legacy_brief(harness):
    client, store, orch = harness

    project = _create_started(
        client,
        "briefed",
        mission="Make local Hive setup dependable.",
        iteration_goal="Prove agents can register and run a probe.",
    )
    _pump(client, store)

    event = orch.invocations[0][0]
    assert "approved intake" in event
    assert "Legacy start brief was ignored" in event
    assert "Make local Hive setup dependable" not in event
    assert "Prove agents can register" not in event
    assert store.get(Project, project["id"]).name == "briefed"


def test_answer_appends_raw_input_log_to_writable_spec_repo(harness, tmp_path):
    client, store, _orch = harness
    origin = tmp_path / "spec-origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(origin), str(seed)], check=True, capture_output=True)
    (seed / "mission.md").write_text("# Mission\nKeep answers durable.\n")
    subprocess.run(["git", "add", "-A"], cwd=seed, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "seed"],
        cwd=seed,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True, capture_output=True)

    project = store.put(Project(name="durable", spec_repo=str(origin)))
    question = store.put(Question(project_id=project.id, text="Which storage path should answers use?"))

    assert client.post(
        f"/api/questions/{question.id}/answer",
        json={"answer": "Append raw answers to input-log before planning resumes."},
    ).json()["status"] == "answered"

    files = subprocess.run(
        ["git", "--git-dir", str(origin), "ls-tree", "-r", "--name-only", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    input_logs = [path for path in files if path.startswith("input-log/")]
    assert len(input_logs) == 1
    logged = subprocess.run(
        ["git", "--git-dir", str(origin), "show", f"main:{input_logs[0]}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Which storage path" in logged
    assert "Append raw answers to input-log" in logged


def test_orchestrator_requires_api_key_before_client(tmp_path):
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="gemini-3-flash-preview", runner_token="test-token", data_dir=tmp_path,
        orch_provider="gemini",
    )
    orch = Orchestrator(store, LocalBlobStore(tmp_path / "blobs"), config)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        orch._generate(project, [], "event", Tools(store, project, spec=None))


def test_openai_orchestrator_tool_loop(tmp_path):
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="gpt-test", runner_token="test-token", data_dir=tmp_path,
        orch_provider="openai", openai_api_key="test-key",
    )
    adapter = ScriptedOpenAIAdapter(
        "test-key",
        "https://api.openai.com/v1",
        "gpt-test",
        responses=[
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "create_workstream",
                                        "arguments": '{"title":"Basics","description":"local setup"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
            {
                "choices": [{"message": {"role": "assistant", "content": "planned"}}],
                "usage": {"prompt_tokens": 1500, "completion_tokens": 300},
            },
        ],
    )
    orch = AdapterOrchestrator(store, LocalBlobStore(tmp_path / "blobs"), config, adapter)

    result = orch._generate(project, [], "event", Tools(store, project, spec=None))

    assert result.text == "planned"
    assert result.model == "gpt-test"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (2500, 500)  # summed
    orch._record_cost(project, result)
    [run] = store.list(OrchestratorRun, project_id=project.id)
    assert run.input_tokens == 2500 and run.output_tokens == 500 and run.cost_usd == 0.0
    assert store.list(Workstream, project_id=project.id)[0].title == "Basics"
    assert adapter.posts[0]["model"] == "gpt-test"
    assert adapter.posts[0]["tools"][0]["type"] == "function"
    assert any(
        m["role"] == "tool" and "workstream_id=" in m["content"] for m in adapter.posts[1]["messages"]
    )


def test_openai_orchestrator_auto_selects_model(tmp_path):
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
        orch_provider="openai", openai_api_key="test-key",
    )
    adapter = ScriptedOpenAIAdapter(
        "test-key",
        "https://api.openai.com/v1",
        "",
        responses=[{"choices": [{"message": {"role": "assistant", "content": "ok"}}]}],
        models={
            "data": [
                {"id": "gpt-image-test", "created": 999},
                {"id": "text-embedding-test", "created": 998},
                {"id": "o-test-newer", "created": 30},
                {"id": "gpt-test-new", "created": 20},
            ]
        },
    )
    orch = AdapterOrchestrator(store, LocalBlobStore(tmp_path / "blobs"), config, adapter)

    assert orch._generate(project, [], "event", Tools(store, project, spec=None)).text == "ok"
    assert adapter.posts[0]["model"] == "gpt-test-new"


def test_orchestrator_falls_back_when_first_provider_is_out_of_quota(tmp_path):
    """The build loop must survive one provider going down. When the preferred
    adapter raises ProviderUnavailable before any tool ran, the orchestrator
    retries the next adapter instead of failing the whole invocation. Regression
    for the OpenAI 429 insufficient_quota that stalled the project."""
    from hive.llm import Completion, ProviderUnavailable, Usage

    class _OutOfQuota:
        model = "gpt-5.5"

        def start(self, *a):
            pass

        def step(self):
            raise ProviderUnavailable("OpenAI-compatible API error 429: insufficient_quota")

        def add_tool_results(self, results):
            pass

    class _Works(_OutOfQuota):
        model = "gemini-3.1-pro-preview"

        def step(self):
            return Completion(text="planned the work", usage=Usage(10, 5))

    class _FallbackOrch(Orchestrator):
        def _build_adapters(self):
            return [_OutOfQuota(), _Works()]

    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="g",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
        openai_api_key="k",
    )
    orch = _FallbackOrch(store, LocalBlobStore(tmp_path / "blobs"), config)
    result = orch._generate(project, [], "event", Tools(store, project, spec=None))
    assert result.text == "planned the work"
    assert result.model == "gemini-3.1-pro-preview"  # fell back to the working provider


def test_openai_orchestrator_requires_api_key_for_official_api(tmp_path):
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="gpt-test", runner_token="test-token", data_dir=tmp_path,
        orch_provider="openai", openai_api_key="", openai_base_url="https://api.openai.com/v1",
    )
    orch = Orchestrator(store, LocalBlobStore(tmp_path / "blobs"), config)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        orch._generate(project, [], "event", Tools(store, project, spec=None))


def test_human_todo_tool_and_api(harness):
    client, store, _orch = harness
    project = store.put(Project(name="p", spec_repo="https://example.com/spec.git"))
    tools = Tools(store, project, spec=None)
    out = tools.create_human_task("Log in codex on vm-1", "run `codex login`", org_wide=True)
    task_id = out.split("=")[1].split()[0]

    assert "Log in codex on vm-1" in tools.snapshot()
    assert client.get("/api/human-todos").json()[0]["status"] == "open"

    # Another project's scoped todo is invisible here; org-wide ones are shared.
    other = store.put(Project(name="other", spec_repo="https://example.com/o.git"))
    Tools(store, other, spec=None).create_human_task("Grant repo access", "add bot to o.git")
    assert "Grant repo access" not in tools.snapshot()
    assert "Grant repo access" in Tools(store, other, spec=None).snapshot()

    assert client.post(f"/api/human-todos/{task_id}/done").json()["status"] == "done"
    assert "Log in codex" not in tools.snapshot()  # only open todos are shown
    detail = client.get(f"/api/projects/{other.id}").json()
    assert detail["human_todos"][0]["title"] == "Grant repo access"


def test_rate_limited_result_sets_cooldown(harness):
    client, store, orch = harness
    _create_started(client, "p2")
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
    assert res["last_exhaustion_text"] == "429 rate limit"
    assert res["last_exhaustion_task_id"] == task["id"]
    assert res["last_exhaustion_at"] > 0


def test_duplicate_task_result_is_ignored(harness):
    client, store, _orch = harness
    project = store.put(Project(name="duplicate-result", spec_repo="https://example.com/spec.git"))
    ws = store.put(Workstream(project_id=project.id, title="build"))
    rid = _register_usable_runner(client, name="dup-runner")
    resource = store.list(Resource)[0]
    task = store.put(
        Task(
            project_id=project.id,
            workstream_id=ws.id,
            repo="https://example.com/app.git",
            instructions="implement feature",
            status=TaskStatus.running,
            runner_id=rid,
        )
    )

    assert client.post(
        f"/api/tasks/{task.id}/result",
        json={"text": "done", "cost_usd": 1.0},
        headers=RUNNER_HEADERS,
    ).json() == {"ok": True}
    ignored = client.post(
        f"/api/tasks/{task.id}/result",
        json={
            "text": "429 rate limit",
            "is_error": True,
            "resource_exhausted": True,
            "cost_usd": 2.0,
        },
        headers=RUNNER_HEADERS,
    ).json()

    assert ignored["ignored"] is True
    finished = store.get(Task, task.id)
    assert finished.status == TaskStatus.done
    assert finished.result_text == "done"
    updated = store.get(Resource, resource.id)
    assert updated.total_tasks == resource.total_tasks + 1
    assert updated.total_cost_usd == resource.total_cost_usd + 1.0
    assert updated.cooldown_until == 0
    assert updated.last_exhaustion_text == ""


def test_structured_verify_result_sets_verdict_without_marker(harness):
    client, store, _orch = harness
    project = store.put(Project(name="structured-verify", spec_repo="https://example.com/spec.git"))
    ws = store.put(Workstream(project_id=project.id, title="build"))
    task = store.put(
        Task(
            project_id=project.id,
            workstream_id=ws.id,
            repo="https://example.com/app.git",
            instructions="verify feature",
            kind=TaskKind.verify,
            status=TaskStatus.running,
        )
    )

    assert client.post(
        f"/api/tasks/{task.id}/result",
        json={
            "text": "Looks good. No legacy marker here.",
            "structured_result": {
                "task_id": task.id,
                "outcome": "accept",
                "acceptance_checked": ["feature works"],
                "commands_run": ["pytest"],
            },
        },
        headers=RUNNER_HEADERS,
    ).json() == {"ok": True}

    saved = store.get(Task, task.id)
    assert saved.verdict == Verdict.accept
    assert saved.structured_result["outcome"] == "accept"


# Real message from `codex exec` when the ChatGPT subscription window is exhausted.
CODEX_QUOTA_ERROR = (
    "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
    "to purchase more credits or try again at 3:28 PM."
)


def test_codex_quota_exhaustion_blocks_project(harness):
    """End-to-end view of a codex quota hit: task fails, resource cools down,
    project blocks on resources, orchestrator is woken with the failure."""
    client, store, orch = harness
    project = _create_started(client, "codex-quota")
    pid = project["id"]
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")

    ws = store.put(Workstream(project_id=pid, title="build"))
    task = store.put(
        Task(
            project_id=pid,
            workstream_id=ws.id,
            repo="https://example.com/app.git",
            backend="codex",
            instructions="implement feature",
        )
    )
    _pump(client, store)
    polled = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert polled["id"] == task.id

    invocations_before = len(orch.invocations)
    client.post(
        f"/api/tasks/{task.id}/result",
        json={
            "text": CODEX_QUOTA_ERROR,
            "is_error": True,
            "resource_exhausted": True,
        },
        headers=RUNNER_HEADERS,
    )

    finished = store.get(Task, task.id)
    assert finished.status == TaskStatus.failed
    assert finished.is_error
    assert "usage limit" in finished.result_text

    codex_res = next(
        r for r in client.get("/api/resources").json()["resources"] if r["backend"] == "codex"
    )
    assert codex_res["usability_status"] == "usable"  # quota ≠ broken login
    assert not codex_res["available"]
    assert codex_res["cooldown_until"] > time.time()
    assert codex_res["last_exhaustion_text"] == CODEX_QUOTA_ERROR
    assert codex_res["last_exhaustion_task_id"] == task.id
    assert codex_res["last_exhaustion_at"] > 0

    # Another codex task is stuck until the cooldown lifts.
    store.put(
        Task(
            project_id=pid,
            workstream_id=ws.id,
            repo="https://example.com/other.git",
            backend="codex",
            instructions="follow-up work",
        )
    )
    _pump(client, store)
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["project"]["state"] == "blocked_resources"
    assert store.list(Task, project_id=pid, status=TaskStatus.pending)

    assert len(orch.invocations) > invocations_before
    wake_text = orch.invocations[-1][0]
    assert "failed" in wake_text
    assert "usage limit" in wake_text

    # A later successful probe proves the temporary availability cooldown is stale.
    queued = client.post(f"/api/resources/{codex_res['id']}/probe").json()
    probe = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert probe["id"] == queued["task"]["id"]
    client.post(
        f"/api/tasks/{probe['id']}/result",
        json={"text": PROBE_MARKER},
        headers=RUNNER_HEADERS,
    )
    codex_res = next(
        r for r in client.get("/api/resources").json()["resources"] if r["backend"] == "codex"
    )
    assert codex_res["usability_status"] == "usable"
    assert codex_res["available"]
    assert codex_res["cooldown_until"] == 0
    assert codex_res["last_exhaustion_text"] == ""
    assert codex_res["last_exhaustion_task_id"] == ""
    assert codex_res["last_exhaustion_at"] == 0

    _pump(client, store)
    assert store.list(Task, project_id=pid, status=TaskStatus.running)


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

    queued = client.post(f"/api/resources/{resource.id}/probe").json()
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task["id"] == queued["task"]["id"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": PROBE_MARKER},
        headers=RUNNER_HEADERS,
    )
    resource = store.get(Resource, resource.id)
    assert resource.usability_status == "usable"
    assert resource.available()
    assert store.list(HumanTask)[0].status == HumanTaskStatus.done

    patched = client.patch(
        f"/api/resources/{resource.id}",
        json={"enabled": False, "disabled_reason": "No subscription"},
    ).json()
    assert patched["enabled"] is False
    assert patched["available"] is False
    assert patched["disabled_reason"] == "No subscription"
    assert store.get(Resource, resource.id).enabled is False
    assert store.list(HumanTask)[0].status == HumanTaskStatus.done

    assert client.post(f"/api/resources/{resource.id}/probe").status_code == 409


def test_resource_exhausted_probe_is_availability_not_login_failure(harness):
    client, store, _orch = harness
    rid = client.post(
        "/api/runners/register",
        json={"name": "quota-probe-runner", "backends": ["codex"]},
        headers=RUNNER_HEADERS,
    ).json()["runner_id"]
    resource = client.get("/api/resources").json()["resources"][0]

    queued = client.post(f"/api/resources/{resource['id']}/probe").json()
    probe = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert probe["id"] == queued["task"]["id"]
    client.post(
        f"/api/tasks/{probe['id']}/result",
        json={
            "text": CODEX_QUOTA_ERROR,
            "is_error": True,
            "resource_exhausted": True,
        },
        headers=RUNNER_HEADERS,
    )

    resource = store.get(Resource, resource["id"])
    assert resource.usability_status == "usable"
    assert not resource.available()
    assert resource.cooldown_until > time.time()
    assert resource.last_exhaustion_text == CODEX_QUOTA_ERROR
    assert not store.list(HumanTask)


def test_local_runner_start_endpoint(tmp_path):
    from hive.api import create_app

    class FakeLocalRunner:
        runner_name = "local-host"

        def __init__(self):
            self.starts = 0
            self.stops = 0
            self.autostart = False

        def status(self, *, message=""):
            return {
                "supported": True,
                "running": self.starts > 0,
                "registered": False,
                "runner_name": self.runner_name,
                "pid": 123 if self.starts > 0 else 0,
                "autostart": self.autostart,
                "log_path": str(tmp_path / "local-runner.log"),
                "message": message,
            }

        def set_autostart(self, enabled):
            self.autostart = enabled
            return self.status(message="local runner autostart updated")

        def start(self):
            self.starts += 1
            return self.status(message="local runner starting")

        def stop(self):
            self.stops += 1

    store = MemoryStore()
    supervisor = Supervisor(store, ScriptedOrchestrator(store).invoke)
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    local_runner = FakeLocalRunner()
    client = TestClient(create_app(store, supervisor, config, local_runner=local_runner))

    assert client.get("/api/resources").json()["local_runner"]["registered"] is False
    started = client.post("/api/local-runner/start").json()
    assert started["running"] is True
    assert started["registered"] is False
    assert local_runner.starts == 1

    client.post(
        "/api/runners/register",
        json={"name": "local-host", "backends": ["codex"]},
        headers=RUNNER_HEADERS,
    )
    resources = client.get("/api/resources").json()
    assert resources["local_runner"]["registered"] is True

    again = client.post("/api/local-runner/start").json()
    assert again["message"] == "local runner already registered"
    assert local_runner.starts == 1


def test_local_runner_autostart_endpoint_starts_runner(tmp_path):
    from hive.api import create_app

    class FakeLocalRunner:
        runner_name = "local-host"

        def __init__(self):
            self.starts = 0
            self.autostart = False

        def status(self, *, message=""):
            return {
                "supported": True,
                "running": self.starts > 0,
                "registered": False,
                "runner_name": self.runner_name,
                "pid": 123 if self.starts > 0 else 0,
                "autostart": self.autostart,
                "log_path": str(tmp_path / "local-runner.log"),
                "message": message,
            }

        def set_autostart(self, enabled):
            self.autostart = enabled
            return self.status(message="local runner autostart updated")

        def start(self):
            self.starts += 1
            return self.status(message="local runner starting")

        def stop(self):
            pass

    store = MemoryStore()
    supervisor = Supervisor(store, ScriptedOrchestrator(store).invoke)
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    local_runner = FakeLocalRunner()
    client = TestClient(create_app(store, supervisor, config, local_runner=local_runner))

    updated = client.patch("/api/local-runner", json={"autostart": True}).json()
    assert updated["autostart"] is True
    assert updated["running"] is True
    assert updated["registered"] is False
    assert local_runner.starts == 1

    updated = client.patch("/api/local-runner", json={"autostart": False}).json()
    assert updated["autostart"] is False
    assert updated["running"] is True
    assert local_runner.starts == 1


def test_local_runner_autostart_writes_machine_config(tmp_path, monkeypatch):
    from hive.config.file import load_stored_config
    from hive.runner._local import LocalRunnerManager

    config_file = tmp_path / "config.env"
    monkeypatch.setenv("HIVE_CONFIG_FILE", str(config_file))
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    manager = LocalRunnerManager(config)

    status = manager.set_autostart(True)

    assert status["autostart"] is True
    assert config.autostart_runner is True
    assert load_stored_config(config_file)["HIVE_AUTOSTART_RUNNER"] == "true"
    assert config_file.stat().st_mode & 0o777 == 0o600


def test_cancel_pending_task(harness):
    client, store, _orch = harness
    _create_started(client, "c")
    _pump(client, store)  # orchestrator queues a work task; no runner online → stays pending
    task = store.list(Task)[0]
    assert task.status == "pending"
    assert client.post(f"/api/tasks/{task.id}/cancel").json()["status"] == "cancelled"


def test_dismiss_question_wakes(harness):
    client, store, _orch = harness
    pid = client.post("/api/projects", json={"name": "d"}).json()["id"]
    sup = client.app.state.supervisor
    q = store.put(Question(project_id=pid, text="pick A or B?"))
    sup._events.clear()
    assert client.post(f"/api/questions/{q.id}/dismiss").json()["status"] == "dismissed"
    assert sup._events.get(pid)  # orchestrator is woken to reconsider the parked workstream


def test_trace_roundtrip(harness):
    client, store, _orch = harness
    pid = client.post("/api/projects", json={"name": "t"}).json()["id"]
    ws = store.put(Workstream(project_id=pid, title="w"))
    task = store.put(Task(project_id=pid, workstream_id=ws.id, repo="r", instructions="i",
                          status=TaskStatus.running))
    trace = b'{"event":"run_init"}\n{"event":"agent_run_end","cost_usd":0.1}\n'
    assert client.post(
        f"/api/tasks/{task.id}/trace", content=trace, headers=RUNNER_HEADERS
    ).json()["ok"]
    assert store.get(Task, task.id).trace_blob == f"workspaces/default/traces/{task.id}.jsonl"
    got = client.get(f"/api/tasks/{task.id}/trace")
    assert got.status_code == 200 and b"run_init" in got.content
    # Trace upload is a runner action — unauthenticated callers are rejected.
    assert client.post(f"/api/tasks/{task.id}/trace", content=trace).status_code == 401


def test_human_todo_done_wakes_project(harness):
    client, store, _orch = harness
    pid = client.post("/api/projects", json={"name": "h"}).json()["id"]
    task = client.post("/api/human-todos", json={"title": "login", "project_id": pid}).json()
    sup = client.app.state.supervisor
    sup._events.clear()
    client.post(f"/api/human-todos/{task['id']}/done")
    assert sup._events.get(pid)  # completing the action re-evaluates work that waited on it


def test_manual_spec_files_finalize_intake_and_handoff_to_orchestrator(harness, tmp_path):
    client, store, orch = harness
    origin = _spec_origin(tmp_path, {
        "mission.md": "# Mission\nBuild Hive.\n",
        "iteration.md": "# Iteration\nMake intake file-based.\n",
    })
    project = client.post("/api/projects", json={"name": "intake-demo"}).json()
    pid = project["id"]
    _configure_project(client, pid, str(origin))

    accepted = client.post(f"/api/projects/{pid}/intake/finalize")
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["spec_status"]["ready"] is True
    assert body["conversation"]["backend"] == "manual"
    assert body["conversation"]["status"] == "done"
    assert store.get(Project, pid).intake_conversation_id == body["conversation"]["id"]

    # Finalization is chief-side: no scout task is required, but normal planning
    # wakes from the same durable-spec event.
    _pump(client, store)
    assert any("Intake accepted from durable spec files" in event for batch in orch.invocations for event in batch)


def test_web_intake_contract_holds_until_durable_spec_finalize(harness, tmp_path):
    """Regression proof for the web intake MVP contract: configuring a project
    is quiet, scout turns stay in intake, write-mission is the durable-spec
    push turn, and only verified files wake normal planning."""
    client, store, orch = harness
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "mission.md").write_text("# Mission\nBuild Hive.\n")
    project = client.post("/api/projects", json={"name": "intake-demo"}).json()
    pid = project["id"]
    _configure_project(client, pid, str(spec_dir))
    _pump(client, store)
    assert orch.invocations == []
    assert store.list(Task, project_id=pid) == []

    rid = _register_usable_runner(client, backend="codex")

    conversation = client.post(f"/api/projects/{pid}/intake/start").json()
    assert conversation["backend"] == "codex"
    assert conversation["model"] == "gpt-5.5"
    assert store.get(Project, pid).state == "intake"
    assert [
        task.kind for task in store.list(Task, project_id=pid)
    ] == [TaskKind.intake]

    _pump(client, store)
    first = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert first["kind"] == "intake"
    assert first["conversation_turn"] == "initial"
    client.post(
        f"/api/tasks/{first['id']}/result",
        json={"text": "We should make intake file-based.", "session_handle": "session-1"},
        headers=RUNNER_HEADERS,
    )
    answer = client.post(
        f"/api/conversations/{conversation['id']}/message",
        json={"action": "message", "message": "Keep wiki/intake.md as provenance."},
    ).json()
    assert answer["task"]["conversation_turn"] == "message"
    _pump(client, store)
    message_task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{message_task['id']}/result",
        json={"text": "Updated brief. I will keep wiki/intake.md as provenance."},
        headers=RUNNER_HEADERS,
    )
    transcript = store.get(AgentConversation, conversation["id"]).transcript
    assert [turn["role"] for turn in transcript] == ["assistant", "user", "assistant"]
    assert "Keep wiki/intake.md" in transcript[1]["text"]
    assert store.get(Project, pid).state == "intake"
    assert orch.invocations == []

    queued = client.post(f"/api/projects/{pid}/intake/write-mission").json()["task"]
    assert queued["conversation_turn"] == "write_mission"
    assert queued["session_handle"] == "session-1"
    assert store.get(AgentConversation, conversation["id"]).status == "finalizing"
    _pump(client, store)
    write_task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert write_task["id"] == queued["id"]
    assert "mission.md" in write_task["instructions"]
    assert "iteration.md" in write_task["instructions"]
    assert "Commit and push" in write_task["instructions"]
    (spec_dir / "iteration.md").write_text("# Iteration\nMake intake file-based.\n")
    (spec_dir / "wiki").mkdir()
    (spec_dir / "wiki" / "intake.md").write_text("# Intake\nConversation captured.\n")
    client.post(
        f"/api/tasks/{write_task['id']}/result",
        json={"text": "Committed and pushed mission.md, iteration.md, and wiki/intake.md."},
        headers=RUNNER_HEADERS,
    )
    assert store.get(AgentConversation, conversation["id"]).status == "open"
    assert store.get(Project, pid).state == "intake"
    _pump(client, store)
    assert orch.invocations == []

    accepted = client.post(f"/api/projects/{pid}/intake/finalize").json()
    assert accepted["spec_status"]["ready"] is True
    assert accepted["conversation"]["status"] == "done"
    assert store.get(Project, pid).state == "idle"
    _pump(client, store)
    assert any("Intake accepted from durable spec files" in event for batch in orch.invocations for event in batch)


def test_approve_without_spec_files_queues_finalize_and_proceed_stays_conversational(
    harness, tmp_path
):
    """Approval is one action: with durable spec files missing, approve queues
    the scout's finalize turn (write + push the files) instead of bouncing the
    user into a separate write-mission step. Proceed remains conversational —
    canonical readiness still comes from dedicated spec files, not from a
    regex-shaped brief."""
    client, store, _orch = harness
    origin = _spec_origin(tmp_path, {"mission.md": "# Mission\nBuild Hive.\n"})
    project = client.post("/api/projects", json={"name": "intake-questions"}).json()
    pid = project["id"]
    _configure_project(client, pid, str(origin))
    rid = _register_usable_runner(client, backend="codex")
    conversation = client.post(f"/api/projects/{pid}/intake/start").json()

    _pump(client, store)
    first = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{first['id']}/result",
        json={
            "text": (
                "Mission:\nBuild Hive.\n\n"
                "Next iteration:\nMake intake work.\n\n"
                "Likely next steps:\n- Wire scout turns\n\n"
                "Assumptions:\n- Push mode is acceptable.\n\n"
                "Questions:\nShould this include mobile UI?"
            ),
        },
        headers=RUNNER_HEADERS,
    )

    proceed = client.post(f"/api/conversations/{conversation['id']}/message", json={"action": "proceed"})
    assert proceed.status_code == 200
    proceed_task = proceed.json()["task"]
    assert proceed_task["conversation_turn"] == "proceed"
    assert "Return a compact updated brief" in proceed_task["instructions"]
    assert "Do not edit files" in proceed_task["instructions"]
    _pump(client, store)
    polled = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{polled['id']}/result",
        json={"text": "Updated brief with assumptions."},
        headers=RUNNER_HEADERS,
    )

    approved = client.post(f"/api/conversations/{conversation['id']}/message", json={"action": "approve"})
    assert approved.status_code == 200
    body = approved.json()
    assert body["spec_status"]["ready"] is False
    assert body["task"]["conversation_turn"] == "finalize"
    assert "mission.md" in body["task"]["instructions"]
    assert "Commit and push" in body["task"]["instructions"]
    assert store.get(AgentConversation, conversation["id"]).status == "finalizing"


def test_spec_handed_at_creation_reaches_scout_and_single_approve_ships_it(harness, tmp_path):
    """The spec-only journey (wiki/ideal-ux.md): a spec given at creation is the
    scout's primary context on turn 1 — no blind turn against an empty repo —
    and one approve drives finalize -> push -> planning without further user
    steps."""
    client, store, orch = harness
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    project = client.post(
        "/api/projects",
        json={"name": "spec-first", "spec_text": "# TD game\nBuild a tower defense game."},
    ).json()
    assert project["initial_spec"] == "# TD game\nBuild a tower defense game."
    pid = project["id"]
    _configure_project(client, pid, str(spec_dir))
    rid = _register_usable_runner(client, backend="codex")

    conversation = client.post(f"/api/projects/{pid}/intake/start").json()
    _pump(client, store)
    first = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert "Build a tower defense game." in first["instructions"]
    assert "primary statement of intent" in first["instructions"]
    client.post(
        f"/api/tasks/{first['id']}/result",
        json={"text": "Brief: build the TD game. No material questions."},
        headers=RUNNER_HEADERS,
    )

    approved = client.post(
        f"/api/conversations/{conversation['id']}/message", json={"action": "approve"}
    ).json()
    assert approved["task"]["conversation_turn"] == "finalize"
    assert "input-log/" in approved["task"]["instructions"]
    _pump(client, store)
    finalize = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert finalize["id"] == approved["task"]["id"]
    (spec_dir / "mission.md").write_text("# Mission\nA polished TD game.\n")
    (spec_dir / "iteration.md").write_text("# Iteration 1\nPlayable core loop.\n")
    client.post(
        f"/api/tasks/{finalize['id']}/result",
        json={"text": "Pushed mission.md and iteration.md at abc123."},
        headers=RUNNER_HEADERS,
    )
    assert store.get(AgentConversation, conversation["id"]).status == "done"
    assert store.get(Project, pid).state == "idle"
    _pump(client, store)
    assert any(
        "Intake accepted and pushed" in event for batch in orch.invocations for event in batch
    )


def test_intake_failure_todo_self_heals_on_successful_retry(harness, tmp_path):
    """A failed intake turn files an operator todo; the retried, successful
    turn closes it again — a fixed condition must not leave a zombie entry in
    Needs-you (live regression: rust-td's checkout-failure todo outlived the
    fix, 2026-07-05)."""
    client, store, _orch = harness
    origin = _spec_origin(tmp_path, {"mission.md": "# Mission\nBuild.\n"})
    project = client.post("/api/projects", json={"name": "flaky-intake"}).json()
    pid = project["id"]
    _configure_project(client, pid, str(origin))
    rid = _register_usable_runner(client, backend="codex")

    first = client.post(f"/api/projects/{pid}/intake/start").json()
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "checkout failed: no HEAD", "is_error": True},
        headers=RUNNER_HEADERS,
    )
    todos = [t for t in store.list(HumanTask) if t.title == "Intake scout failed for flaky-intake"]
    assert [t.status for t in todos] == [HumanTaskStatus.open]

    retry = client.post(f"/api/projects/{pid}/intake/start").json()
    assert retry["id"] != first["id"]
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "Brief: repo inspected. Questions: none."},
        headers=RUNNER_HEADERS,
    )
    todos = [t for t in store.list(HumanTask) if t.title == "Intake scout failed for flaky-intake"]
    assert [t.status for t in todos] == [HumanTaskStatus.done]


CLAUDE_SUBSCRIPTION_DISABLED = (
    "Your organization has disabled Claude subscription access for Claude Code · "
    "Use an Anthropic API key instead, or ask your admin to enable access"
)


def _probe_backend_usable(client, rid, backend):
    """Probe the (rid, backend) resource to usable and return its id."""
    resource = next(
        r for r in client.get("/api/resources").json()["resources"]
        if r["runner_id"] == rid and r["backend"] == backend
    )
    client.post(f"/api/resources/{resource['id']}/probe")
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(f"/api/tasks/{task['id']}/result", json={"text": PROBE_MARKER}, headers=RUNNER_HEADERS)
    return resource["id"]


def test_intake_auth_block_marks_backend_failed_escalates_and_retries(harness):
    """The exact incident: the claude scout is blocked by an org/subscription
    policy. That backend must be marked failed (not a transient cooldown), an
    operator todo filed, and the user able to retry intake on another scout."""
    client, store, _orch = harness
    project = client.post("/api/projects", json={"name": "hive"}).json()
    pid = project["id"]
    _configure_project(client, pid, "https://github.com/ikamensh/hive")
    rid = client.post(
        "/api/runners/register",
        json={"name": "raven", "backends": ["claude", "codex"]},
        headers=RUNNER_HEADERS,
    ).json()["runner_id"]
    claude_res = _probe_backend_usable(client, rid, "claude")
    _probe_backend_usable(client, rid, "codex")

    # User picks claude explicitly (they have a subscription and expect it to work).
    conversation = client.post(f"/api/projects/{pid}/intake/start", json={"backend": "claude"}).json()
    assert conversation["backend"] == "claude"

    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task["kind"] == "intake" and task["backend"] == "claude"
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": CLAUDE_SUBSCRIPTION_DISABLED, "is_error": True, "auth_blocked": True},
        headers=RUNNER_HEADERS,
    )

    failed = store.get(AgentConversation, conversation["id"])
    assert failed.status == "failed"
    assert "subscription access" in failed.latest_brief

    # The blocked credential is failed, not a silently-expiring cooldown.
    res = store.get(Resource, claude_res)
    assert res.usability_status == "failed"
    assert not res.available()
    assert res.cooldown_until == 0
    # The failure text is now the resource's latest usability evidence —
    # `hive show` must quote the real reason, not the long-gone happy probe.
    assert "subscription access" in res.last_probe_text

    # An operator todo names the fix, scoped org-wide (the login, not this project).
    todo = next(t for t in store.list(HumanTask) if t.title == "Fix claude login on raven")
    assert todo.project_id == ""
    assert "subscription access" in todo.instructions

    # Retry without a backend now auto-falls back to the still-usable codex scout.
    retry = client.post(f"/api/projects/{pid}/intake/start", json={}).json()
    assert retry["id"] != conversation["id"]
    assert retry["backend"] == "codex"
    assert retry["status"] in ("open", "running")
    assert store.get(Project, pid).intake_conversation_id == retry["id"]


def test_login_todo_for_renamed_runner_still_closes():
    """Regression (live fleet): a runner rename (laptop-raven -> raven) left a
    'Fix gemini-cli login on laptop-raven' todo no event could ever close,
    because auto-close matched only the current runner name. Resolving the
    backend's resource also sweeps its login todos naming runners that no
    longer exist — while todos for other live runners stay open."""
    from hive.models import HumanTask, Resource, Runner
    from hive.runner._task_results import complete_resource_login_todos

    store = MemoryStore()
    runner = store.put(Runner(name="raven", backends=["gemini-cli"]))
    store.put(Runner(name="hive-vm", backends=["gemini-cli"]))
    resource = store.put(Resource(runner_id=runner.id, backend="gemini-cli"))
    for title in (
        "Fix gemini-cli login on raven",  # this runner: closes
        "Fix gemini-cli login on laptop-raven",  # zombie runner name: swept
        "Fix gemini-cli login on hive-vm",  # other live runner: stays open
        "Fix claude login on laptop-raven",  # other backend: not this resource's call
    ):
        store.put(HumanTask(title=title, instructions="", project_id=""))

    complete_resource_login_todos(store, resource)

    status = {t.title: t.status for t in store.list(HumanTask)}
    assert status["Fix gemini-cli login on raven"] == "done"
    assert status["Fix gemini-cli login on laptop-raven"] == "done"
    assert status["Fix gemini-cli login on hive-vm"] == "open"
    assert status["Fix claude login on laptop-raven"] == "open"


def test_intake_failed_conversation_is_restartable(harness):
    """A failed intake conversation must not 409 the user into a dead end: a
    fresh start mints a new conversation; messaging the dead one is rejected
    with a pointer to retry."""
    client, store, _orch = harness
    project = client.post("/api/projects", json={"name": "restart"}).json()
    pid = project["id"]
    _configure_project(client, pid, "https://example.com/spec.git")
    rid = _register_usable_runner(client, backend="codex")

    conversation = client.post(f"/api/projects/{pid}/intake/start").json()
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "scout crashed", "is_error": True},
        headers=RUNNER_HEADERS,
    )
    assert store.get(AgentConversation, conversation["id"]).status == "failed"

    # Messaging a failed conversation is a clear 409 pointing at the retry path.
    rejected = client.post(f"/api/conversations/{conversation['id']}/message", json={"action": "proceed"})
    assert rejected.status_code == 409
    assert "retry" in rejected.json()["detail"]

    # Restarting mints a fresh conversation the project now points at.
    restarted = client.post(f"/api/projects/{pid}/intake/start").json()
    assert restarted["id"] != conversation["id"]
    assert restarted["status"] in ("open", "running")
    assert store.get(Project, pid).intake_conversation_id == restarted["id"]


def test_intake_start_rejects_unknown_backend(harness):
    client, store, _orch = harness
    project = client.post("/api/projects", json={"name": "badbackend"}).json()
    pid = project["id"]
    _configure_project(client, pid, "https://example.com/spec.git")
    _register_usable_runner(client, backend="codex")
    bad = client.post(f"/api/projects/{pid}/intake/start", json={"backend": "gemini-cli"})
    assert bad.status_code == 400
    assert "trusted scout" in bad.json()["detail"]


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


def test_overview_reflects_project_and_capacity(harness):
    """The home overview is one request that sees a started project and a
    probed-usable agent on its machine."""
    client, store, _ = harness
    _create_started(client, "demo")
    _pump(client, store)
    _register_usable_runner(client)

    ov = client.get("/api/overview").json()

    assert [p["name"] for p in ov["projects"]] == ["demo"]
    assert ov["capacity"]["agents_total"] >= 1
    assert ov["capacity"]["agents_ready"] >= 1
    assert ov["totals"]["machines_online"] >= 1
    # Totals stay internally consistent with the rows they summarize.
    assert ov["totals"]["tasks_running"] == sum(p["counts"]["running"] for p in ov["projects"])


def test_subscription_records_licensing_mode(harness):
    """A subscription persists how its credential is licensed, so the recovery
    flow can later decide self-serve vs ask-a-human without re-guessing."""
    client, store, _ = harness
    created = client.post(
        "/api/subscriptions",
        json={"provider": "claude", "plan": "Claude Max 5x", "licensing_mode": "machine_bound"},
    ).json()
    assert created["licensing_mode"] == "machine_bound"
    assert store.get(Subscription, created["id"]).licensing_mode == "machine_bound"

    # Omitting it is allowed and defaults to unknown rather than erroring.
    bare = client.post("/api/subscriptions", json={"provider": "cursor"}).json()
    assert bare["licensing_mode"] == "unknown"


def test_resources_surface_unsubscribed_usable_providers(harness):
    """/api/resources offers a usable-but-unrecorded provider as a candidate the
    user can confirm, carrying the provider-rulebook licensing default; once a
    subscription exists the candidate disappears."""
    client, store, _ = harness
    runner = store.put(Runner(name="laptop", backends=["cursor"]))
    store.put(
        Resource(
            runner_id=runner.id,
            backend="cursor",
            usability_status=ResourceUsability.usable,
        )
    )

    candidates = client.get("/api/resources").json()["subscription_candidates"]
    assert [c["provider"] for c in candidates] == ["cursor"]
    assert candidates[0]["licensing_mode"] == "portable"  # Cursor key is portable
    assert candidates[0]["evidence"] == "usable on laptop"

    client.post("/api/subscriptions", json={"provider": "cursor"})
    assert client.get("/api/resources").json()["subscription_candidates"] == []


def test_project_payload_regression_work_verify_accept(harness):
    """Regression test ensuring the project detail payload contract matches the
    expected schema and updates correctly throughout a work-verify-accept cycle.
    """
    client, store, _orch = harness

    # 1. Create and start a project
    project = _create_started(client, "regression-demo")
    pid = project["id"]
    _pump(client, store)

    # 2. Get project payload and verify all fields are present
    payload = client.get(f"/api/projects/{pid}").json()
    expected_keys = {
        "project",
        "workstreams",
        "work_items",
        "tasks",
        "questions",
        "human_todos",
        "conversations",
        "issue_runs",
        "stories",
        "findings",
        "test_episodes",
        "directives",
        "checkouts",
        "spend_today",
    }
    assert expected_keys.issubset(payload.keys())
    assert payload["project"]["name"] == "regression-demo"
    assert len(payload["work_items"]) == 1
    assert len(payload["tasks"]) == 1

    # 3. Register a runner, poll for task, verify running status in payload
    rid = _register_usable_runner(client)
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert task is not None and task["kind"] == "work"

    payload = client.get(f"/api/projects/{pid}").json()
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["id"] == task["id"]

    # 4. Finish work task -> verify task gets queued
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "project payload regression verified", "cost_usd": 0.05},
        headers=RUNNER_HEADERS,
    )
    _pump(client, store)
    verify = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert verify is not None and verify["kind"] == "verify"

    # Verify both tasks exist in the payload
    payload = client.get(f"/api/projects/{pid}").json()
    assert len(payload["tasks"]) == 2
    assert any(t["kind"] == "verify" for t in payload["tasks"])

    # 5. Finish verify task with ACCEPT verdict -> parsed into payload correctly
    client.post(
        f"/api/tasks/{verify['id']}/result",
        json={"text": "VERDICT: ACCEPT"},
        headers=RUNNER_HEADERS,
    )
    _pump(client, store)

    payload = client.get(f"/api/projects/{pid}").json()
    verify_task_payload = next(t for t in payload["tasks"] if t["kind"] == "verify")
    assert verify_task_payload["verdict"] == "accept"
    assert store.get(Task, verify["id"]).verdict == "accept"



def test_finalize_that_did_not_land_reopens_intake_instead_of_waking_planning(harness, tmp_path):
    """Trust but verify (G18): a finalize turn that *claims* success while the
    spec repo has no durable files (e.g. the push 403'd) must not flip the
    project to planning — intake reopens with an operator todo naming the fix."""
    client, store, orch = harness
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()  # stays empty: the "push failed" repo state
    project = client.post("/api/projects", json={"name": "pushless"}).json()
    pid = project["id"]
    _configure_project(client, pid, str(spec_dir))
    rid = _register_usable_runner(client, backend="codex")

    conversation = client.post(f"/api/projects/{pid}/intake/start").json()
    _pump(client, store)
    first = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{first['id']}/result",
        json={"text": "Brief. No questions."},
        headers=RUNNER_HEADERS,
    )
    approved = client.post(
        f"/api/conversations/{conversation['id']}/message", json={"action": "approve"}
    ).json()
    _pump(client, store)
    finalize = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert finalize["id"] == approved["task"]["id"]
    client.post(
        f"/api/tasks/{finalize['id']}/result",
        json={"text": "Committed locally. Push did not complete: 403 denied."},
        headers=RUNNER_HEADERS,
    )

    conv = store.get(AgentConversation, conversation["id"])
    assert conv.status == "open"
    assert "does not verify" in conv.transcript[-1]["text"]
    assert store.get(Project, pid).state == "intake"
    _pump(client, store)
    assert not any("Intake accepted and pushed" in e for batch in orch.invocations for e in batch)
    todo = next(t for t in store.list(HumanTask) if "finalize did not land" in t.title)
    assert "push access" in todo.instructions


def test_intake_retry_carries_prior_answers(harness, tmp_path):
    """A fresh intake conversation (after a failed/reopened round) seeds the
    scout with the user's earlier answers instead of re-asking them (G20 —
    observed live when gleaner's spec repo was re-pointed to a fork)."""
    client, store, _orch = harness
    origin = _spec_origin(tmp_path, {"mission.md": "# Mission\nBuild.\n"})
    project = client.post("/api/projects", json={"name": "retry"}).json()
    pid = project["id"]
    _configure_project(client, pid, str(origin))
    rid = _register_usable_runner(client, backend="codex")

    first = client.post(f"/api/projects/{pid}/intake/start").json()
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "Brief. Question: last-write-wins for duplicates?"},
        headers=RUNNER_HEADERS,
    )
    client.post(
        f"/api/conversations/{first['id']}/message",
        json={"action": "message", "message": "Yes — last-write-wins; counters count unique ids."},
    )
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    client.post(
        f"/api/tasks/{task['id']}/result",
        json={"text": "Updated brief.", "is_error": True},  # round dies mid-flight
        headers=RUNNER_HEADERS,
    )

    retry = client.post(f"/api/projects/{pid}/intake/start").json()
    assert retry["id"] != first["id"]
    _pump(client, store)
    fresh = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert "do not re-ask" in fresh["instructions"]
    assert "last-write-wins; counters count unique ids" in fresh["instructions"]
