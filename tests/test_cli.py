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
from hive._control.supervisor import Supervisor

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

    detail = cli(client, "project", pid)
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

    question = cli(client, "project", pid)["questions"][0]
    cli(client, "answer", question["id"], "yes, add B")
    _pump(client, store)
    assert cli(client, "project", pid)["project"]["goal_complete"]

    cli(client, "iterate", pid, "now add C")
    _pump(client, store)
    assert not cli(client, "project", pid)["project"]["goal_complete"]

    # `hive show` is the subsystem introspection view; a part argument selects
    # one section, no argument returns all of them.
    full = cli(client, "show")
    assert set(full) == {"machines", "agents", "subscriptions", "autonomy"}
    assert cli(client, "show", "agents") == full["agents"]
    assert full["agents"]["launchable_now"] >= 1  # the registered fake runners
    assert any(j["job"] == "dark_machine_watch" for j in full["autonomy"])


def test_cli_parks_and_revives_a_resource(harness):
    """Parking a resource (e.g. a retired CLI tier) takes it out of dispatch
    but keeps it visible with its reason in `hive show agents`; re-enabling
    clears the reason."""
    client, store = harness
    _register_usable_runner(client, name="fake")
    resource = cli(client, "resources")["resources"][0]

    parked = cli(client, "resource-disable", resource["id"], "--reason", "tier retired")
    assert parked["enabled"] is False
    assert parked["disabled_reason"] == "tier retired"
    assert parked["available"] is False

    agents = {a["resource_id"]: a for a in cli(client, "show", "agents")["agents"]}
    assert agents[resource["id"]]["status"] == "disabled"
    assert agents[resource["id"]]["note"] == "tier retired"

    revived = cli(client, "resource-enable", resource["id"])
    assert revived["enabled"] is True and revived["disabled_reason"] == ""


def test_format_show_renders_readable_summary():
    """Default `hive show` output is a human summary, not JSON: every section
    header present, the actionable facts (chief tag, agent notes, launchable
    count, idle reasons) readable, and no JSON syntax anywhere."""
    from hive.cli import format_show

    payload = {
        "machines": [
            {"name": "hive-vm", "device_kind": "server", "os": "linux", "online": True,
             "dark": False, "retired": False, "hosts_chief": True, "last_seen": 0.0,
             "runners": [{"backends": ["codex", "claude"]}]},
            {"name": "raven", "device_kind": "laptop", "os": "macos", "online": False,
             "dark": True, "retired": False, "hosts_chief": False, "last_seen": 0.0,
             "runners": []},
        ],
        "agents": {
            "launchable_now": 1,
            "agents": [
                {"backend": "codex", "machine": "hive-vm", "status": "ready", "available": True, "note": ""},
                {"backend": "claude", "machine": "hive-vm", "status": "failed", "available": False,
                 "note": "Not logged in · Please run /login"},
            ],
        },
        "subscriptions": {
            "subscriptions": [
                {"provider": "claude", "plan": "Claude Max", "licensing_mode": "machine_bound",
                 "notes": "", "serving": [],
                 "login_needed": [{"machine": "hive-vm", "note": "Not logged in"}]},
                {"provider": "cursor", "plan": "", "licensing_mode": "portable",
                 "notes": "", "serving": ["raven"],
                 "login_needed": [{"machine": "hive-vm", "note": "never probed"}]},
            ],
            "unregistered": [{"provider": "codex", "evidence": "usable on hive-vm"}],
            "unowned": ["gemini-cli"],
        },
        "autonomy": [
            {"job": "ci_check", "project_id": "p1", "project_name": "hive", "interval_s": 300.0,
             "action_now": "poll CI", "reason": "", "backends": ["codex"], "machines": ["hive-vm"]},
            {"job": "testing_check", "project_id": "p1", "project_name": "hive", "interval_s": 900.0,
             "action_now": "", "reason": "no daily budget", "backends": ["codex"], "machines": []},
        ],
    }

    text = format_show(payload, None)
    for fact in (
        "MACHINES", "AGENTS — 1 of 2 launchable now", "SUBSCRIPTIONS", "AUTONOMY",
        "[chief]", "DARK",
        "Not logged in · Please run /login",
        "serving: NOWHERE",  # claude sub with no machine able to serve it
        "hive login claude --machine hive-vm",  # machine_bound gap -> the one-command fix
        "provide the key/login on hive-vm (portable)",  # portable gap -> key hint, not SSH login
        "usable on hive-vm",
        "unowned — no subscription, usable nowhere: gemini-cli",
        "every 5m via codex on hive-vm: poll CI",
        "idle — no daily budget",
    ):
        assert fact in text
    assert "{" not in text  # readable, not JSON

    # a selected part renders alone
    assert format_show(payload["machines"], "machines").startswith("MACHINES")
    assert "AUTONOMY" not in format_show(payload["agents"], "agents")
    assert "hive login claude" in format_show(payload["subscriptions"], "subscriptions")


def test_login_ssh_argv_recipes():
    """Each recipe channels the interaction correctly: a TTY always (-t), the
    codex OAuth callback port forwarded, credentials landing in the runner's
    user (sudo -i), and HIVE_VM* coordinates overridable like deploy/vm.sh."""
    from hive.cli import LOGIN_RECIPES, login_ssh_argv

    for backend in LOGIN_RECIPES:
        argv = login_ssh_argv(backend, "hive-vm", {})
        assert argv[:4] == ["gcloud", "compute", "ssh", "hive-vm"]
        assert "-t" in argv
        assert argv[-1].startswith("sudo -i ")
    codex = login_ssh_argv("codex", "hive-vm", {})
    assert "1455:localhost:1455" in codex  # OAuth callback rides the tunnel
    # regression: every recipe must run a dedicated login command, never launch
    # the agent's full interactive CLI (bare `claude` greeted the operator with
    # a workspace-trust dialog instead of the login flow)
    for backend in LOGIN_RECIPES:
        assert LOGIN_RECIPES[backend]["remote"] != f"sudo -i {backend}"
        assert "login" in LOGIN_RECIPES[backend]["remote"]
    custom = login_ssh_argv("claude", "other-vm", {"HIVE_VM_ZONE": "eu-x", "HIVE_VM_PROJECT": "p1"})
    assert "--zone=eu-x" in custom and "--project=p1" in custom and custom[3] == "other-vm"


def test_login_flow_opens_ssh_then_probes(harness, monkeypatch):
    """`hive login` = SSH session + proof: it must call the recipe's SSH
    command and then queue a probe on the exact (machine, backend) resource.
    Unsupported backends and unknown machines fail with actionable errors."""
    client, store = harness
    _register_usable_runner(client, name="fake", backend="codex")

    calls: list[list[str]] = []
    monkeypatch.setattr("hive.cli.subprocess.call", lambda argv: calls.append(argv) or 0)
    monkeypatch.setattr("hive.cli.time.sleep", lambda s: None)

    result = cli(client, "login", "codex", "--machine", "fake")
    assert calls and calls[0][3] == "fake" and "sudo -i codex login" in calls[0]
    # probe queued and polled; no fake runner executes it here, so it stays probing
    assert result["probe"] == "probing"

    with pytest.raises(SystemExit) as exc:
        cli(client, "login", "gemini-cli", "--machine", "fake")
    assert "portable API key" in str(exc.value)

    with pytest.raises(SystemExit) as exc:
        cli(client, "login", "codex", "--machine", "ghost")
    assert "known machines" in str(exc.value)


def test_resolve_targets_precedence(monkeypatch, tmp_path):
    """An explicit URL (env > stored) names exactly one chief — no discovery
    beyond it. A one-off `HIVE_URL=…` still overrides the saved default."""
    from hive.cli import DEFAULT_HIVE_URL, resolve_targets

    monkeypatch.setenv("HIVE_RUNNER_ENV_FILE", str(tmp_path / "absent.env"))
    assert [t.base_url for t in resolve_targets({}, {})] == [DEFAULT_HIVE_URL]

    stored = {"HIVE_URL": "https://hive.example", "HIVE_BASIC_AUTH": "ilya:pw", "HIVE_TOKEN": "tok"}
    [saved] = resolve_targets({}, stored)
    assert saved.base_url == "https://hive.example"
    assert saved.auth == ("ilya", "pw")
    assert saved.token == "tok"

    [overridden] = resolve_targets({"HIVE_URL": "http://localhost:9000"}, stored)
    assert overridden.base_url == "http://localhost:9000"
    assert overridden.auth == ("ilya", "pw")  # unrelated keys still come from stored


def test_unconfigured_cli_discovers_chief_from_runner_env(monkeypatch, tmp_path):
    """A machine with an installed runner already knows its chief: with no
    HIVE_URL configured, the CLI tries localhost (dev loop) first and then the
    runner.env chief — carrying that file's own perimeter credentials, not the
    CLI's. An explicit URL suppresses discovery entirely."""
    from hive.cli import DEFAULT_HIVE_URL, resolve_targets

    runner_env = tmp_path / "runner.env"
    runner_env.write_text(
        "HIVE_URL=https://hive.34-62-218-54.sslip.io\n"
        "HIVE_BASIC_AUTH=ilya:secret\n"
        "HIVE_RUNNER_TOKEN=rt\n"
    )
    monkeypatch.setenv("HIVE_RUNNER_ENV_FILE", str(runner_env))

    local, fleet = resolve_targets({}, {})
    assert local.base_url == DEFAULT_HIVE_URL
    assert fleet.base_url == "https://hive.34-62-218-54.sslip.io"
    assert fleet.auth == ("ilya", "secret")
    assert fleet.token == ""  # the runner token is protocol auth, not client auth

    assert [t.base_url for t in resolve_targets({"HIVE_URL": "http://x:1"}, {})] == ["http://x:1"]


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


def _clear_managed_state_env(monkeypatch):
    for key in ("HIVE_GCP_PROJECT", "HIVE_GCS_BUCKET"):
        monkeypatch.delenv(key, raising=False)


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
    _clear_managed_state_env(monkeypatch)

    from hive.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["run", "--host", "127.0.0.1", "--port", "8765"])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Hive requires managed state" in err
    assert "HIVE_GCP_PROJECT" in err
    assert "HIVE_GCS_BUCKET" in err


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
    assert "Hive API unreachable (tried http://127.0.0.1:65533)" in err
    assert "Traceback" not in err


def test_main_falls_back_to_runner_env_chief(monkeypatch, capsys, tmp_path):
    """The user's actual failure: no HIVE_URL configured, no local chief.
    main() must fall through to the runner.env chief instead of dying on
    localhost — and say so on stderr while keeping stdout pure JSON."""
    runner_env = tmp_path / "runner.env"
    runner_env.write_text("HIVE_URL=https://fleet.example\nHIVE_BASIC_AUTH=ilya:pw\n")
    monkeypatch.setenv("HIVE_RUNNER_ENV_FILE", str(runner_env))
    monkeypatch.delenv("HIVE_URL", raising=False)
    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))

    class FakeResponse:
        def raise_for_status(self):
            return self

        def json(self):
            return [{"id": "p1"}]

    class RoutedClient:
        def __init__(self, *, base_url, auth, **kwargs):
            self.base_url, self.auth = base_url, auth

        def get(self, path):
            if "localhost" in self.base_url:
                raise httpx.ConnectError(
                    "connection refused", request=httpx.Request("GET", self.base_url + path)
                )
            assert self.auth == ("ilya", "pw")  # runner.env creds ride along
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", RoutedClient)

    from hive.cli import main

    main(["projects"])
    captured = capsys.readouterr()
    assert '"id": "p1"' in captured.out
    # Walking past a dead localhost candidate is normal discovery, not news:
    # stdout stays pure JSON and stderr stays empty on success.
    assert captured.err == ""


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


def test_doctor_storage_reports_missing_managed_state(monkeypatch, capsys):
    _fake_gh(monkeypatch, "")
    monkeypatch.setattr("hive.cli.load_stored_config", lambda: {})
    _clear_managed_state_env(monkeypatch)

    from hive.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["doctor", "storage"])

    assert exc.value.code == 1
    shown = __import__("json").loads(capsys.readouterr().out)
    assert shown["ok"] is False
    assert "HIVE_GCP_PROJECT" in shown["error"]
    assert "HIVE_GCS_BUCKET" in shown["error"]


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


def test_cli_test_run_and_cancel_map_to_endpoints():
    rec = _Recorder()
    cli(rec, "test-run", "p1", "ws1", "--scope", "full", "--max", "3")
    cli(rec, "test-run", "p1", "ws1", "--story", "login", "--story", "signup")
    cli(rec, "test-cancel", "ep1")
    assert rec.calls == [
        ("POST", "/api/projects/p1/workstreams/ws1/test-episodes",
         {"scope": "full", "story_keys": [], "max_stories": 3}),
        # naming stories implies the selected scope
        ("POST", "/api/projects/p1/workstreams/ws1/test-episodes",
         {"scope": "selected", "story_keys": ["login", "signup"], "max_stories": 0}),
        ("POST", "/api/test-episodes/ep1/cancel", None),
    ]


def test_cli_stories_coverage_view(harness, tmp_path):
    """`hive stories` shows the backlog health offer before any testing exists,
    and the mirrored stories + live episode once a test-run starts."""
    client, store = harness
    origin = _spec_origin(tmp_path, {
        "mission.md": "# Mission\nShip the demo.\n",
        "iteration.md": "# Iteration\nBuild the first loop.\n",
        "acceptance/login.md": (
            "# story: login [api]\n"
            "As a user I can sign in so that I reach my dashboard.\n\n"
            "## Examples\n- Given valid credentials\n  When I sign in\n  Then I see the dashboard\n"
        ),
    })
    pid = cli(client, "create", "demo")["id"]
    cli(client, "set", pid, "--spec-repo", str(origin))

    report = cli(client, "stories", pid)
    stream = report["testing"][0]
    assert stream["health"]["state"] == "missing"  # nothing mirrored yet: the offer stands
    assert "autonomous" in stream["health"]["offer"]

    episode = cli(client, "test-run", pid, stream["workstream_id"], "--scope", "full")["episode"]
    report = cli(client, "stories", pid)
    stream = report["testing"][0]
    assert stream["health"]["state"] == "refreshing"
    assert [s["key"] for s in stream["stories"]] == ["login"]
    assert stream["latest_episode"]["id"] == episode["id"]

    cancelled = cli(client, "test-cancel", episode["id"])
    assert cancelled["status"] == "cancelled"


def test_cli_new_hands_spec_to_intake_in_one_command(harness, tmp_path):
    """`hive new` collapses create + repo wiring + budget + spec handover +
    intake-start into one command; the spec text reaches the scout's first-turn
    instructions verbatim (the spec-only journey in wiki/ideal-ux.md)."""
    client, store = harness
    spec = tmp_path / "spec.md"
    spec.write_text("# Game\nA tower defense game in Rust.\n")
    _register_usable_runner(client, backend="codex")

    out = cli(
        client, "new", "td",
        "--spec", str(spec),
        "--repo", "https://example.com/td.git",
        "--budget", "5",
    )

    detail = client.get(f"/api/projects/{out['project_id']}").json()
    project = detail["project"]
    assert project["spec_repo"] == "https://example.com/td.git"
    assert project["member_repos"] == ["https://example.com/td.git"]
    assert project["daily_budget_usd"] == 5.0
    assert project["initial_spec"].startswith("# Game")
    intake_tasks = [t for t in detail["tasks"] if t["kind"] == "intake"]
    assert len(intake_tasks) == 1
    assert "A tower defense game in Rust." in intake_tasks[0]["instructions"]
    assert out["conversation_id"]
    assert f"hive project {out['project_id']}" in out["next"]
