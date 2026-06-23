"""Scripted CLI run: every human action in the loop goes through hive.cli.

Reuses the e2e harness (scripted orchestrator + fake runner over the real
runner protocol); the CLI plays the user. This is the parity check that the
CLI can fully replace the UI.
"""

import os

import pytest
import httpx
from fastapi.testclient import TestClient
from test_api_e2e import ScriptedOrchestrator, _pump, _register_usable_runner, _spec_origin

from hive.cli import (
    UVICORN_GRACEFUL_SHUTDOWN_S,
    build_parser,
    detect_config,
    load_stored_config,
    prepare_run_env,
    run,
)
from hive.config.settings import Config
from hive.persistence.store import MemoryStore
from hive.control.supervisor import Supervisor

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


def test_cli_drives_full_loop(harness, tmp_path):
    client, store = harness
    origin = _spec_origin(tmp_path, {
        "mission.md": "# Mission\nShip the demo.\n",
        "iteration.md": "# Iteration\nBuild the first loop.\n",
    })

    project = cli(client, "create", "demo")
    pid = project["id"]
    cli(client, "set", pid, "--spec-repo", str(origin),
        "--member-repos", "https://example.com/app.git")

    scout_rid = _register_usable_runner(client, name="scout", backend="codex")
    conversation = cli(client, "intake-start", pid)
    _pump(client, store)
    intake = client.post(f"/api/runners/{scout_rid}/poll", headers=RUNNER_HEADERS).json()["task"]
    assert intake["kind"] == "intake"
    client.post(
        f"/api/tasks/{intake['id']}/result",
        json={
            "text": (
                "Mission:\nShip the demo.\n\n"
                "Next iteration:\nBuild the first loop.\n\n"
                "Likely next steps:\n- Queue the first workstream\n- Verify the loop\n\n"
                "Assumptions:\n- Direct push is acceptable.\n\n"
                "Questions:\n(none)"
            ),
            "session_handle": "mock-intake",
        },
        headers=RUNNER_HEADERS,
    )
    approved = cli(client, "intake-approve", conversation["id"])
    assert approved["conversation"]["status"] == "done"
    assert approved["spec_status"]["ready"] is True
    # The scripted planner queues normal work on cursor; make that resource
    # visible before planning so the resource-aware tool accepts the task.
    rid = _register_usable_runner(client, name="fake")
    cli(client, "start", pid)
    assert cli(client, "projects")[0]["id"] == pid
    _pump(client, store)

    detail = cli(client, "show", pid)
    build_tasks = [task for task in detail["tasks"] if task["kind"] not in ("intake", "probe")]
    assert len(detail["work_items"]) == 1 and len(build_tasks) == 1
    assert detail["workstreams"][0]["kind"] == "iteration"

    # fake runner executes work + verify tasks over the real protocol
    for text in ("implemented, tests pass", "VERDICT: ACCEPT"):
        _pump(client, store)
        task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]
        client.post(f"/api/tasks/{task['id']}/result", json={"text": text},
                    headers=RUNNER_HEADERS)
    _pump(client, store)

    assert cli(client, "resources")["runners"][0]["online"]
    full_task = cli(client, "task", build_tasks[0]["id"])
    assert "implement feature" in full_task["instructions"]

    question = cli(client, "show", pid)["questions"][0]
    cli(client, "answer", question["id"], "yes, add B")
    _pump(client, store)
    assert cli(client, "show", pid)["project"]["goal_complete"]

    cli(client, "iterate", pid, "now add C")
    _pump(client, store)
    assert not cli(client, "show", pid)["project"]["goal_complete"]


def test_resolve_target_precedence():
    """The CLI's client target resolves env > stored > default, so a saved
    remote is the default but a one-off `HIVE_URL=…` still overrides it."""
    from hive.cli import DEFAULT_HIVE_URL, resolve_target

    assert resolve_target({}, {}).base_url == DEFAULT_HIVE_URL

    stored = {"HIVE_URL": "https://hive.example", "HIVE_BASIC_AUTH": "ilya:pw", "HIVE_TOKEN": "tok"}
    saved = resolve_target({}, stored)
    assert saved.base_url == "https://hive.example"
    assert saved.auth == ("ilya", "pw")
    assert saved.token == "tok"

    overridden = resolve_target({"HIVE_URL": "http://localhost:9000"}, stored)
    assert overridden.base_url == "http://localhost:9000"
    assert overridden.auth == ("ilya", "pw")  # unrelated keys still come from stored


def test_client_target_keys_never_reach_server_env(monkeypatch):
    # Regression: persisting a client target (where the CLI *sends* commands)
    # must not leak into a `hive run` server process's environment.
    _fake_gh(monkeypatch, "")
    env: dict[str, str] = {}
    prepare_run_env(env, {
        "HIVE_URL": "https://hive.example",
        "HIVE_BASIC_AUTH": "ilya:pw",
        "HIVE_TOKEN": "tok",
        "HIVE_GCP_PROJECT": "proj",
    })
    assert not ({"HIVE_URL", "HIVE_BASIC_AUTH", "HIVE_TOKEN"} & env.keys())
    assert env["HIVE_GCP_PROJECT"] == "proj"


def test_cli_whoami(harness):
    client, _store = harness
    me = cli(client, "whoami")
    assert me["auth_mode"] == "dev"
    assert me["target"].startswith("http")
    assert me["cli_version"]["version"]
    assert me["version"]["version"]
    assert me["user"]["github_login"]
    assert me["workspace"]["id"]


def test_cli_version_reports_cli_and_chief(harness):
    client, _store = harness
    result = cli(client, "version")

    assert result["target"].startswith("http")
    assert result["cli"]["version"]
    assert result["chief"]["version"] == result["cli"]["version"]


class _Recorder:
    """A minimal httpx-shaped client that records the call instead of sending it
    — enough to assert a CLI command maps to the documented API request."""

    base_url = "http://rec"

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []

    def _send(self, method: str, url: str, json=None):
        self.calls.append((method, url, json))

        class _Resp:
            def raise_for_status(self):
                return self

            def json(self):
                return {"task": {"id": "t1"}}

        return _Resp()

    def get(self, url, **kw):
        return self._send("GET", url, kw.get("json"))

    def post(self, url, **kw):
        return self._send("POST", url, kw.get("json"))

    def patch(self, url, **kw):
        return self._send("PATCH", url, kw.get("json"))

    def delete(self, url, **kw):
        return self._send("DELETE", url, kw.get("json"))


def test_cli_test_refresh_maps_to_endpoint():
    rec = _Recorder()
    result = cli(rec, "test-refresh", "p1", "ws1", "--backend", "codex", "--model", "gpt")
    assert rec.calls == [
        ("POST", "/api/projects/p1/workstreams/ws1/test-refresh",
         {"backend": "codex", "model": "gpt"}),
    ]
    assert result["task"]["id"] == "t1"


def test_main_targets_stored_remote(monkeypatch, capsys):
    """`main` builds its client from the persisted target + bearer token, so a
    saved remote is driven without re-exporting env vars every invocation."""
    import httpx
    from hive.cli import main

    captured: dict = {}

    class _Client:
        def __init__(self, **kw):
            captured.update(kw)
            self.base_url = kw.get("base_url")

        def get(self, *a, **k):
            return httpx.Response(200, json=[], request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr("hive.cli.load_stored_config",
                        lambda *a, **k: {"HIVE_URL": "https://hive.example", "HIVE_TOKEN": "tok"})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _Client(**kw))
    for key in ("HIVE_URL", "HIVE_BASIC_AUTH", "HIVE_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    main(["projects"])
    assert captured["base_url"] == "https://hive.example"
    assert captured["headers"] == {"Authorization": "Bearer tok"}


def test_main_reports_auth_failure_cleanly(monkeypatch, capsys):
    import httpx
    from hive.cli import main

    class _AuthFailClient:
        def __init__(self, **kw):
            self.base_url = kw.get("base_url")

        def get(self, *a, **k):
            return httpx.Response(401, text="nope", request=httpx.Request("GET", "http://x"))

    monkeypatch.setattr("hive.cli.load_stored_config", lambda *a, **k: {})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _AuthFailClient(**kw))
    for key in ("HIVE_URL", "HIVE_BASIC_AUTH", "HIVE_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(SystemExit) as exc:
        main(["projects"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Not authorized" in err and "HIVE_BASIC_AUTH" in err


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


def _fake_gh(monkeypatch, token):
    import subprocess

    rc = 0 if token else 1
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, rc, stdout=token, stderr=""))


def test_prepare_run_env_extracts_gh_token(monkeypatch):
    _fake_gh(monkeypatch, "ghp_abc\n")
    env = {"OPENAI_API_KEY": "sk-x"}
    notes = prepare_run_env(env, {})
    assert env["HIVE_GH_TOKEN"] == "ghp_abc"
    assert any("gh auth token" in n for n in notes)
    assert any("OPENAI_API_KEY from environment" in n for n in notes)
    assert any("MISSING HIVE_GCP_PROJECT" in n for n in notes)
    assert any("MISSING HIVE_GCS_BUCKET" in n for n in notes)


def test_prepare_run_env_no_gh_no_key(monkeypatch):
    _fake_gh(monkeypatch, "")
    env: dict[str, str] = {}
    notes = prepare_run_env(env, {})
    assert "HIVE_GH_TOKEN" not in env
    assert any("NO API key" in n for n in notes)
    assert any("local runner autostart: disabled" in n for n in notes)


def test_stored_config_overrides_ambient_env(monkeypatch):
    # gh would offer a token, but the stored value must win (separate hive key).
    _fake_gh(monkeypatch, "from-gh")
    env = {"OPENAI_API_KEY": "shell-key", "HIVE_GH_TOKEN": "shell-gh"}
    stored = {
        "OPENAI_API_KEY": "hive-key",
        "HIVE_GH_TOKEN": "hive-gh",
        "HIVE_GCP_PROJECT": "proj",
        "HIVE_GCS_BUCKET": "bucket",
        "HIVE_WORKSPACE_ID": "team",
        "HIVE_WORKSPACE_NAME": "Team",
        "HIVE_PUBLIC_URL": "https://hive.example",
    }
    notes = prepare_run_env(env, stored)
    assert env["OPENAI_API_KEY"] == "hive-key"
    assert env["HIVE_GH_TOKEN"] == "hive-gh"
    assert any("OPENAI_API_KEY from stored config" in n for n in notes)
    assert any("Firestore (proj, from stored config)" in n for n in notes)
    assert any("GCS (bucket, from stored config)" in n for n in notes)
    assert any("workspace: team (Team)" in n for n in notes)
    assert any("public url: https://hive.example" in n for n in notes)


def test_stored_config_can_enable_runner_autostart(monkeypatch):
    _fake_gh(monkeypatch, "")
    env: dict[str, str] = {}
    notes = prepare_run_env(env, {"HIVE_AUTOSTART_RUNNER": "true"})
    assert env["HIVE_AUTOSTART_RUNNER"] == "true"
    assert any("local runner autostart: enabled" in n for n in notes)


def test_run_chief_requires_managed_state(monkeypatch, tmp_path, capsys):
    import uvicorn

    _fake_gh(monkeypatch, "")
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))

    from hive.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["run", "--host", "127.0.0.1", "--port", "8765"])

    assert exc.value.code == 2
    assert "Hive requires managed state" in capsys.readouterr().err


def test_run_chief_caps_graceful_shutdown(monkeypatch, tmp_path, capsys):
    import uvicorn

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))

    _fake_gh(monkeypatch, "")
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIVE_GCP_PROJECT", "proj")
    monkeypatch.setenv("HIVE_GCS_BUCKET", "bucket")
    monkeypatch.delenv("HIVE_WEB_DIST", raising=False)

    from hive.cli import main

    main(["run", "--host", "127.0.0.1", "--port", "8765", "--no-web-build"])

    assert calls
    assert calls[0][1]["timeout_graceful_shutdown"] == UVICORN_GRACEFUL_SHUTDOWN_S
    assert "starting hive chief" in capsys.readouterr().out


def test_run_chief_builds_web_bundle(monkeypatch, tmp_path, capsys):
    import subprocess
    import uvicorn

    calls = []
    uvicorn_calls = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs.get("cwd"), kwargs.get("check")))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_run(*args, **kwargs):
        uvicorn_calls.append((args, kwargs))

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr("hive.cli._web_deps_stale", lambda _web_dir: True)
    monkeypatch.setattr("hive.cli.shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIVE_GCP_PROJECT", "proj")
    monkeypatch.setenv("HIVE_GCS_BUCKET", "bucket")
    monkeypatch.setenv("HIVE_GH_TOKEN", "token")
    monkeypatch.delenv("HIVE_WEB_DIST", raising=False)

    from hive.cli import main

    main(["run", "--host", "127.0.0.1", "--port", "8765"])

    npm_calls = [call for call in calls if call[0][0] == "npm"]
    assert [call[0] for call in npm_calls] == [["npm", "ci"], ["npm", "run", "build"]]
    assert uvicorn_calls
    assert "HIVE_WEB_DIST" in os.environ
    out = capsys.readouterr().out
    assert "web: installing npm dependencies" in out
    assert "web: building latest web bundle" in out


def test_run_chief_leader_refusal_is_concise(monkeypatch, tmp_path, capsys):
    import uvicorn

    def fake_run(*args, **kwargs):
        raise RuntimeError("another chief (host:123) holds the leader lease for workspace default")

    _fake_gh(monkeypatch, "")
    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HIVE_GCP_PROJECT", "proj")
    monkeypatch.setenv("HIVE_GCS_BUCKET", "bucket")
    monkeypatch.delenv("HIVE_WEB_DIST", raising=False)

    from hive.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["run", "--host", "127.0.0.1", "--port", "8765", "--no-web-build"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Hive chief did not start" in err
    assert "leader lease" in err
    assert "Traceback" not in err


def test_unreachable_api_prints_concise_error(monkeypatch, capsys):
    request = httpx.Request("GET", "http://127.0.0.1:65533/api/projects")

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, path):
            raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setenv("HIVE_URL", "http://127.0.0.1:65533")
    monkeypatch.setattr(httpx, "Client", BrokenClient)

    from hive.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["projects"])

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Hive API unreachable at http://127.0.0.1:65533" in err
    assert "Traceback" not in err


def test_doctor_storage_uses_managed_state_config(monkeypatch, capsys):
    monkeypatch.setenv("HIVE_GCP_PROJECT", "proj")
    monkeypatch.setenv("HIVE_GCS_BUCKET", "bucket")
    monkeypatch.setenv("HIVE_RUNNER_TOKEN", "runner")
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})
    monkeypatch.setattr(
        "hive.persistence.storage.managed_state_doctor",
        lambda config: {
            "ok": True,
            "gcp_project": config.gcp_project,
            "gcs_bucket": config.gcs_bucket,
            "workspace_id": config.workspace_id,
            "checks": [],
            "leader": None,
        },
    )

    from hive.cli import main

    main(["doctor", "storage"])

    shown = __import__("json").loads(capsys.readouterr().out)
    assert shown["ok"] is True
    assert shown["gcp_project"] == "proj"
    assert shown["gcs_bucket"] == "bucket"


def test_migrate_local_state_command(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_migrate(store, blobs, **kwargs):
        calls.append((store.root, blobs.root, kwargs))
        return {"ok": True, "documents": {}, "blobs": 0, "verified": kwargs["verify"]}

    monkeypatch.setattr("hive.persistence.storage.migrate_local_state", fake_migrate)
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})

    from hive.cli import main

    main([
        "migrate-local-state",
        "--data-dir",
        str(tmp_path),
        "--gcp-project",
        "proj",
        "--gcs-bucket",
        "bucket",
        "--no-verify",
    ])

    assert calls == [
        (
            tmp_path / "store",
            tmp_path / "blobs",
            {
                "gcp_project": "proj",
                "gcs_bucket": "bucket",
                "workspace_id": "default",
                "verify": False,
            },
        )
    ]
    assert __import__("json").loads(capsys.readouterr().out)["verified"] is False


def test_config_command_set_show_unset(tmp_path, monkeypatch, capsys):
    import json as _json

    from hive.cli import _mask, main

    cfg = tmp_path / "config.env"
    monkeypatch.setenv("HIVE_CONFIG_FILE", str(cfg))

    main(["config", "set", "OPENAI_API_KEY", "sk-secret-1234"])
    assert cfg.stat().st_mode & 0o777 == 0o600
    assert load_stored_config(cfg) == {"OPENAI_API_KEY": "sk-secret-1234"}

    capsys.readouterr()
    main(["config", "show"])
    shown = _json.loads(capsys.readouterr().out)
    assert shown == {"OPENAI_API_KEY": "…1234"}  # masked, never the raw secret

    main(["config", "unset", "OPENAI_API_KEY"])
    assert load_stored_config(cfg) == {}
    assert _mask("HIVE_ORCH_MODEL", "gpt-x") == "gpt-x"  # non-secret shown plainly


def test_detect_config_seeds_from_gh_and_env(monkeypatch):
    _fake_gh(monkeypatch, "ghp_detected")
    found = detect_config({"GEMINI_API_KEY": "g-key", "HIVE_ORCH_MODEL": "m", "IRRELEVANT": "x"})
    assert found["HIVE_GH_TOKEN"] == "ghp_detected"
    assert found["GEMINI_API_KEY"] == "g-key"
    assert "IRRELEVANT" not in found


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
