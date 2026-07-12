"""Runner reboot semantics: in-flight tasks are requeued on boot registration,
left alone on heartbeat registration."""

import json
import subprocess
import time

from fastapi.testclient import TestClient

from hive.agents import PROBE_MARKER
from hive.config.settings import Config
from hive.models import (
    Machine,
    Project,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    IssueItem,
)
from hive.persistence.store import MemoryStore
from hive._control.supervisor import Supervisor
from hive.runner._daemon import checkout, run_preflight, validate_probe_result

H = {"X-Hive-Token": "t"}
CLAUDE_CODE_DISCOVERY = {
    "name": "claude",
    "installed": True,
    "status": "ok",
    "path": "/opt/homebrew/bin/claude",
    "version": "2.1.145 (Claude Code)",
}
CODEX_CLI_DISCOVERY = {
    "name": "codex",
    "installed": True,
    "status": "ok",
    "path": "/opt/homebrew/bin/codex",
    "version": "codex-cli 0.139.0",
}


def make_client(store):
    config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
                    orch_model="", runner_token="t", data_dir=None)
    from hive.api import create_app

    return TestClient(create_app(store, Supervisor(store, lambda p, e: None), config))


def test_boot_requeues_inflight_tasks_heartbeat_does_not():
    store = MemoryStore()
    client = make_client(store)
    rid = client.post("/api/runners/register",
                      json={"name": "r", "backends": ["cursor"], "boot": True},
                      headers=H).json()["runner_id"]
    project = store.put(Project(name="p", spec_repo="s"))
    ws = store.put(IssueItem(project_id=project.id, title="w"))
    task = store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                          instructions="i", status=TaskStatus.running,
                          runner_id=rid, delivered=True))

    # heartbeat: task untouched
    client.post("/api/runners/register",
                json={"name": "r", "backends": ["cursor"]}, headers=H)
    assert store.get(Task, task.id).status == TaskStatus.running

    # boot: task requeued
    client.post("/api/runners/register",
                json={"name": "r", "backends": ["cursor"], "boot": True}, headers=H)
    requeued = store.get(Task, task.id)
    assert requeued.status == TaskStatus.pending
    assert requeued.runner_id == "" and not requeued.delivered


def test_register_auto_probes_new_resource_and_records_discovery():
    store = MemoryStore()
    client = make_client(store)
    rid = client.post(
        "/api/runners/register",
        json={
            "name": "r",
            "backends": ["codex"],
            "auto_probe": True,
            "discoveries": [
                {
                    "name": "codex",
                    "installed": True,
                    "status": "ok",
                    "path": "/usr/local/bin/codex",
                    "version": "codex 1.0",
                }
            ],
        },
        headers=H,
    ).json()["runner_id"]

    resource = store.list(Resource)[0]
    assert resource.discovery_status == "ok"
    assert resource.cli_path == "/usr/local/bin/codex"
    assert resource.usability_status == ResourceUsability.probing

    tasks = store.list(Task)
    assert len(tasks) == 1
    assert tasks[0].kind == TaskKind.probe
    assert tasks[0].runner_id == rid
    # A probe carries a sentinel, not a chief filesystem path: the runner
    # builds its own local probe repo, so a runner on a remote machine can probe.
    assert tasks[0].repo == "probe:local"

    client.post(
        "/api/runners/register",
        json={"name": "r", "backends": ["codex"], "auto_probe": True},
        headers=H,
    )
    assert len(store.list(Task)) == 1

    polled = client.post(f"/api/runners/{rid}/poll", headers=H).json()["task"]
    assert polled["id"] == tasks[0].id
    client.post(
        f"/api/tasks/{polled['id']}/result",
        json={"text": PROBE_MARKER},
        headers=H,
    )
    assert store.get(Resource, resource.id).usability_status == ResourceUsability.usable


def test_boot_auto_probes_stale_claude_and_codex_states_after_healthy_discovery():
    store = MemoryStore()
    client = make_client(store)
    rid = client.post(
        "/api/runners/register",
        json={
            "name": "raven",
            "backends": ["claude", "codex"],
            "discoveries": [CLAUDE_CODE_DISCOVERY, CODEX_CLI_DISCOVERY],
        },
        headers=H,
    ).json()["runner_id"]
    resources = {resource.backend: resource for resource in store.list(Resource)}
    claude = resources["claude"]
    claude.usability_status = ResourceUsability.usable
    claude.cooldown_until = time.time() + 3600
    claude.last_exhaustion_text = (
        "Your organization has disabled Claude subscription access for Claude Code"
    )
    store.put(claude)
    codex = resources["codex"]
    codex.usability_status = ResourceUsability.failed
    codex.last_probe_text = (
        "warning: `--full-auto` is deprecated; use `--sandbox workspace-write` instead."
    )
    store.put(codex)

    client.post(
        "/api/runners/register",
        json={
            "name": "raven",
            "backends": ["claude", "codex"],
            "boot": True,
            "auto_probe": True,
            "discoveries": [CLAUDE_CODE_DISCOVERY, CODEX_CLI_DISCOVERY],
        },
        headers=H,
    )

    probes = store.list(Task)
    assert {task.backend for task in probes} == {"claude", "codex"}
    assert all(task.kind == TaskKind.probe and task.runner_id == rid for task in probes)
    assert {
        resource.backend: resource.usability_status
        for resource in store.list(Resource)
    } == {"claude": ResourceUsability.probing, "codex": ResourceUsability.probing}

    for task in probes:
        client.post(f"/api/tasks/{task.id}/result", json={"text": PROBE_MARKER}, headers=H)

    payload = client.get("/api/resources").json()["resources"]
    by_backend = {resource["backend"]: resource for resource in payload}
    assert by_backend["claude"]["available"] is True
    assert by_backend["claude"]["cooldown_until"] == 0
    assert by_backend["codex"]["available"] is True

    client.post(
        "/api/runners/register",
        json={
            "name": "raven",
            "backends": ["claude", "codex"],
            "auto_probe": True,
            "discoveries": [CLAUDE_CODE_DISCOVERY, CODEX_CLI_DISCOVERY],
        },
        headers=H,
    )
    assert len(store.list(Task)) == 2


def test_register_records_machine_metadata():
    store = MemoryStore()
    client = make_client(store)
    client.post(
        "/api/runners/register",
        json={
            "name": "raven",
            "backends": ["codex"],
            "machine_id": "machine-raven",
            "machine_name": "raven",
            "machine_type": "macbook",
            "machine_os": "macos",
            "machine_arch": "arm64",
            "machine_kind": "laptop",
        },
        headers=H,
    )

    machine = store.get(Machine, "machine-raven")
    assert machine is not None
    assert machine.machine_type == "macbook"
    assert machine.os == "macos"
    assert machine.arch == "arm64"
    assert machine.device_kind == "laptop"

    payload = client.get("/api/resources").json()
    resource_machine = next(m for m in payload["machines"] if m["id"] == "machine-raven")
    assert resource_machine["machine_type"] == "macbook"
    assert resource_machine["device_kind"] == "laptop"


def test_forget_machine_cascades_to_runner_and_resources():
    store = MemoryStore()
    client = make_client(store)
    client.post(
        "/api/runners/register",
        json={
            "name": "raven",
            "backends": ["codex", "claude"],
            "machine_id": "machine-raven",
            "machine_name": "raven",
        },
        headers=H,
    )
    assert store.get(Machine, "machine-raven") is not None
    assert store.list(Resource, workspace_id="default")

    deleted = client.request("DELETE", "/api/machines/machine-raven")
    assert deleted.status_code == 200

    assert store.get(Machine, "machine-raven") is None
    assert store.list(Runner, workspace_id="default") == []
    assert store.list(Resource, workspace_id="default") == []
    assert client.get("/api/resources").json()["machines"] == []
    assert client.request("DELETE", "/api/machines/machine-raven").status_code == 404


def test_codex_probe_explains_deprecated_wrapper(tmp_path):
    text, is_error = validate_probe_result(
        tmp_path,
        "warning: `--full-auto` is deprecated; use `--sandbox workspace-write` instead.",
        False,
        backend="codex",
    )

    assert is_error
    assert "kodo Codex wrapper" in text


def test_runner_preflight_visibly_checks_issue_commenting_auth(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Logged in\n", stderr="")
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "o/r",
                        "viewerPermission": "WRITE",
                        "hasIssuesEnabled": True,
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    def fake_git(args, cwd, timeout=120):
        calls.append(["git", *args])
        return subprocess.CompletedProcess(["git", *args], 0, stdout="", stderr="ok")

    monkeypatch.setattr("hive.runner._daemon.subprocess.run", fake_run)
    monkeypatch.setattr("hive.runner._daemon._git", fake_git)
    monkeypatch.setattr("hive.runner._daemon.time.time", lambda: 123)

    result = run_preflight(tmp_path)

    assert result["is_error"] is False
    assert "PASS gh issue commenting auth: o/r permission=WRITE issues=enabled" in result["text"]
    assert ["gh", "repo", "view", "--json", "nameWithOwner,viewerPermission,hasIssuesEnabled"] in calls


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_fresh_issue_checkout_resets_existing_branch_and_preserves_backup(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True, text=True)
    _git(["config", "user.email", "test@example.invalid"], seed)
    _git(["config", "user.name", "Test"], seed)
    (seed / "file.txt").write_text("main v1\n")
    _git(["add", "file.txt"], seed)
    _git(["commit", "-m", "main v1"], seed)
    _git(["remote", "add", "origin", str(remote)], seed)
    _git(["push", "-u", "origin", "main"], seed)
    _git(["--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], tmp_path)

    _git(["checkout", "-b", "hive/issue-9"], seed)
    (seed / "file.txt").write_text("old issue attempt\n")
    _git(["commit", "-am", "old issue attempt"], seed)
    _git(["push", "-u", "origin", "hive/issue-9"], seed)
    old_issue = _git(["rev-parse", "HEAD"], seed).stdout.strip()

    _git(["checkout", "main"], seed)
    (seed / "file.txt").write_text("main v2\n")
    _git(["commit", "-am", "main v2"], seed)
    _git(["push", "origin", "main"], seed)
    new_main = _git(["rev-parse", "HEAD"], seed).stdout.strip()

    monkeypatch.setattr("hive.runner._daemon.WORKDIR", tmp_path / "work")
    monkeypatch.setattr("hive.runner._daemon.time.time", lambda: 123456)

    dirty_path = checkout(str(remote), "hive/issue-9")
    (dirty_path / "file.txt").write_text("dirty cancelled review\n")
    (dirty_path / "scratch.txt").write_text("left behind\n")

    path = checkout(str(remote), "hive/issue-9", fresh_branch=True)

    assert (path / "file.txt").read_text() == "main v2\n"
    assert not (path / "scratch.txt").exists()
    assert _git(["rev-parse", "HEAD"], path).stdout.strip() == new_main
    assert _git(["--git-dir", str(remote), "rev-parse", "hive/issue-9"], tmp_path).stdout.strip() == new_main
    backup = "hive/issue-9-previous-123456"
    assert _git(["--git-dir", str(remote), "rev-parse", backup], tmp_path).stdout.strip() == old_issue


def test_default_branch_checkout_lands_head_on_main_not_a_stale_branch(tmp_path, monkeypatch):
    """A no-branch task ("work that lands on main") must leave HEAD *on* the
    default branch even when the persistent checkout was left on an issue branch
    by a prior task. Otherwise the agent's `git push HEAD` lands on that stale
    branch — observed live: a test_refresh pushed its acceptance refresh onto a
    leftover hive/issue-18 instead of main, so the spec home never updated."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True, text=True)
    _git(["config", "user.email", "test@example.invalid"], seed)
    _git(["config", "user.name", "Test"], seed)
    (seed / "file.txt").write_text("main v1\n")
    _git(["add", "file.txt"], seed)
    _git(["commit", "-m", "main v1"], seed)
    _git(["remote", "add", "origin", str(remote)], seed)
    _git(["push", "-u", "origin", "main"], seed)
    _git(["--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], tmp_path)
    _git(["checkout", "-b", "hive/issue-9"], seed)
    _git(["push", "-u", "origin", "hive/issue-9"], seed)

    monkeypatch.setattr("hive.runner._daemon.WORKDIR", tmp_path / "work")

    # A prior issue task left the persistent checkout sitting on the issue branch.
    issue_path = checkout(str(remote), "hive/issue-9")
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], issue_path).stdout.strip() == "hive/issue-9"

    # A no-branch task reuses that checkout; it must end up on main.
    path = checkout(str(remote), "")
    assert _git(["rev-parse", "--abbrev-ref", "HEAD"], path).stdout.strip() == "main"

    # So a commit + `git push HEAD` lands on origin/main, not the stale branch.
    _git(["config", "user.email", "test@example.invalid"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "acceptance.txt").write_text("refreshed\n")
    _git(["add", "acceptance.txt"], path)
    _git(["commit", "-m", "refresh acceptance"], path)
    pushed = _git(["rev-parse", "HEAD"], path).stdout.strip()
    _git(["push", "origin", "HEAD"], path)
    assert _git(["--git-dir", str(remote), "rev-parse", "main"], tmp_path).stdout.strip() == pushed
    assert _git(["--git-dir", str(remote), "rev-parse", "hive/issue-9"], tmp_path).stdout.strip() != pushed


def test_boot_marks_interrupted_probe_unknown_then_queues_fresh_probe():
    store = MemoryStore()
    client = make_client(store)
    client.post(
        "/api/runners/register",
        json={"name": "r", "backends": ["cursor"], "auto_probe": True},
        headers=H,
    ).json()["runner_id"]
    resource = store.list(Resource)[0]
    first_probe = store.list(Task)[0]

    client.post(
        "/api/runners/register",
        json={"name": "r", "backends": ["cursor"], "boot": True, "auto_probe": True},
        headers=H,
    )

    interrupted = store.get(Task, first_probe.id)
    assert interrupted.status == TaskStatus.failed
    assert store.get(Resource, resource.id).usability_status == ResourceUsability.probing
    probes = store.list(Task)
    assert len(probes) == 2
    assert probes[-1].id != first_probe.id
    assert probes[-1].status == TaskStatus.running


def test_poll_persists_last_seen_only_when_stale():
    """Regression: the poll loop used to write the runner document every
    second, around the clock — ~86K Firestore writes per idle runner-day.
    A polling runner is self-evidently alive, register() heartbeats every
    30s, and ONLINE_WINDOW_S is 90s, so a persisted last_seen may lag by
    RUNNER_LAST_SEEN_REFRESH_S without ever appearing offline."""

    class RunnerPutCountingStore(MemoryStore):
        def __init__(self):
            super().__init__()
            self.runner_puts = 0

        def put(self, obj):
            if isinstance(obj, Runner):
                self.runner_puts += 1
            return super().put(obj)

    store = RunnerPutCountingStore()
    client = make_client(store)
    rid = client.post(
        "/api/runners/register",
        json={"name": "r", "backends": ["cursor"], "boot": True},
        headers=H,
    ).json()["runner_id"]
    project = store.put(Project(name="p", spec_repo="s"))
    ws = store.put(IssueItem(project_id=project.id, title="w"))

    def deliverable_task():
        return store.put(
            Task(project_id=project.id, workstream_id=ws.id, repo="r",
                 instructions="i", status=TaskStatus.running, runner_id=rid)
        )

    # Fresh last_seen (just registered): polling must not rewrite the runner.
    deliverable_task()
    store.runner_puts = 0
    assert client.post(f"/api/runners/{rid}/poll", headers=H).json()["task"]
    assert store.runner_puts == 0

    # Stale last_seen: polling refreshes it, once.
    def age(runner):
        runner.last_seen = time.time() - 30
    store.update(Runner, rid, age)
    deliverable_task()
    store.runner_puts = 0
    assert client.post(f"/api/runners/{rid}/poll", headers=H).json()["task"]
    assert store.runner_puts == 1
    assert time.time() - store.get(Runner, rid).last_seen < 5


def test_register_response_advertises_chief_urls():
    """The register response is how runners learn where the chief lives:
    HIVE_ADVERTISED_URLS when set, else the chief's public_url. Runners
    persist these as reconnect candidates (see hive/worker/roster.py),
    so a relocated chief only has to advertise itself."""
    store = MemoryStore()
    config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
                    orch_model="", runner_token="t", data_dir=None,
                    advertised_urls="https://hive.example, https://hive-alt.example/")
    from hive.api import create_app

    client = TestClient(create_app(store, Supervisor(store, lambda p, e: None), config))
    data = client.post("/api/runners/register",
                       json={"name": "r", "backends": ["cursor"]}, headers=H).json()
    assert data["chief_urls"] == ["https://hive.example", "https://hive-alt.example"]

    # Unset: falls back to public_url — but never advertises loopback, which
    # points at the wrong host on every other machine (a default local chief
    # once polluted a runner's roster with http://localhost:8000).
    for public_url, expected in [
        ("https://hive.example", ["https://hive.example"]),
        ("http://localhost:8000", []),
        ("http://127.0.0.1:8000", []),
    ]:
        store = MemoryStore()
        config_default = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
                                orch_model="", runner_token="t", data_dir=None,
                                public_url=public_url)
        client = TestClient(create_app(store, Supervisor(store, lambda p, e: None),
                                       config_default))
        data = client.post("/api/runners/register",
                           json={"name": "r", "backends": ["cursor"]}, headers=H).json()
        assert data["chief_urls"] == expected


def test_poll_response_carries_chief_version():
    """Every poll response (task or not) carries the chief's version, so a
    self-updating runner notices a redeployed chief within one poll cycle —
    the fleet runs mixed versions for seconds, not a periodic-timer interval."""
    from hive.version import get_version

    store = MemoryStore()
    client = make_client(store)
    rid = client.post("/api/runners/register",
                      json={"name": "r", "backends": ["cursor"], "boot": True},
                      headers=H).json()["runner_id"]

    empty = client.post(f"/api/runners/{rid}/poll", headers=H).json()
    assert empty["task"] is None
    assert empty["chief_version"] == get_version()

    project = store.put(Project(name="p", spec_repo="s"))
    ws = store.put(IssueItem(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                   instructions="i", status=TaskStatus.running, runner_id=rid))
    loaded = client.post(f"/api/runners/{rid}/poll", headers=H).json()
    assert loaded["task"] is not None
    assert loaded["chief_version"] == get_version()
