"""Scripted CLI run: every human action in the loop goes through hive.cli.

Reuses the e2e harness (scripted orchestrator + fake runner over the real
runner protocol); the CLI plays the user. This is the parity check that the
CLI can fully replace the UI.
"""

import pytest
from fastapi.testclient import TestClient
from test_api_e2e import ScriptedOrchestrator, _pump, _register_usable_runner

from hive.cli import build_parser, run
from hive.config import Config
from hive.store import MemoryStore
from hive.supervisor import Supervisor

RUNNER_HEADERS = {"X-Hive-Token": "test-token"}


def cli(client, *argv: str):
    return run(build_parser().parse_args(argv), client)


@pytest.fixture
def harness(tmp_path):
    store = MemoryStore()
    supervisor = Supervisor(store, ScriptedOrchestrator(store).invoke)
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    from hive.api import create_app

    yield TestClient(create_app(store, supervisor, config)), store


def test_cli_drives_full_loop(harness):
    client, store = harness

    project = cli(client, "create", "demo")
    pid = project["id"]
    cli(client, "set", pid, "--spec-repo", "https://example.com/spec.git",
        "--member-repos", "https://example.com/app.git")
    cli(client, "start", pid, "--mission", "Ship the demo", "--iteration-goal", "Build the first loop")
    assert cli(client, "projects")[0]["id"] == pid
    _pump(client, store)

    detail = cli(client, "show", pid)
    assert len(detail["workstreams"]) == 1 and len(detail["tasks"]) == 1

    # fake runner executes work + verify tasks over the real protocol
    rid = _register_usable_runner(client, name="fake")
    for text in ("implemented, tests pass", "VERDICT: ACCEPT"):
        _pump(client, store)
        task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
        client.post(f"/api/tasks/{task['id']}/result", json={"text": text},
                    headers=RUNNER_HEADERS)
    _pump(client, store)

    assert cli(client, "resources")["runners"][0]["online"]
    full_task = cli(client, "task", detail["tasks"][0]["id"])
    assert "implement feature" in full_task["instructions"]

    question = cli(client, "show", pid)["questions"][0]
    cli(client, "answer", question["id"], "yes, add B")
    _pump(client, store)
    assert cli(client, "show", pid)["project"]["goal_complete"]

    cli(client, "iterate", pid, "now add C")
    _pump(client, store)
    assert not cli(client, "show", pid)["project"]["goal_complete"]


def test_cli_agents_and_probe(harness):
    client, _store = harness
    agents = cli(client, "agents")
    assert "cursor" in agents["supported"]
    assert isinstance(agents["detected"], list)

    rid = client.post("/api/runners/register",
                      json={"name": "fake", "backends": ["cursor"]},
                      headers=RUNNER_HEADERS).json()["runner_id"]
    resource = cli(client, "resources")["resources"][0]
    queued = cli(client, "probe", resource["id"])
    assert queued["task"]["kind"] == "probe"
    assert client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]["id"] == queued["task"]["id"]


def test_cli_settings_and_admin(harness):
    client, _store = harness
    pid = cli(client, "create", "p")["id"]
    cli(client, "set", pid, "--spec-repo", "https://example.com/s.git")

    patched = cli(client, "set", pid, "--paused", "true", "--autonomy", "pr",
                  "--member-repos", "https://example.com/a.git,https://example.com/b.git")
    assert patched["paused"] and patched["autonomy"] == "pr"
    assert len(patched["member_repos"]) == 2

    cli(client, "feedback", pid, "some-task-id", "reject", "--comment", "wrong direction")

    sub = cli(client, "sub-add", "anthropic", "--plan", "max")
    assert cli(client, "subs")[0]["provider"] == "anthropic"
    cli(client, "sub-rm", sub["id"])
    assert cli(client, "subs") == []

    todo = cli(client, "todo-add", "Log in codex", "--instructions", "run `codex login`")
    assert cli(client, "todos")[0]["status"] == "open"
    assert cli(client, "todo-done", todo["id"])["status"] == "done"

    cli(client, "org-context-set", "We ship daily.")
    assert cli(client, "org-context")["text"] == "We ship daily."

    patched = cli(client, "set", pid, "--daily-budget", "12.5")
    assert patched["daily_budget_usd"] == 12.5


def test_cli_cancel_and_dismiss(harness):
    from hive.models import Question, Task, Workstream

    client, store = harness
    pid = cli(client, "create", "p")["id"]
    cli(client, "set", pid, "--spec-repo", "https://example.com/s.git")
    ws = store.put(Workstream(project_id=pid, title="w"))
    task = store.put(Task(project_id=pid, workstream_id=ws.id, repo="r", instructions="i"))
    assert cli(client, "cancel", task.id)["status"] == "cancelled"
    question = store.put(Question(project_id=pid, text="A or B?"))
    assert cli(client, "dismiss", question.id)["status"] == "dismissed"
