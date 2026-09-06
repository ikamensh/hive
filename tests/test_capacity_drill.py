"""Controlled capacity evidence never masquerades as provider telemetry."""

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from hive._control.supervisor import Supervisor
from hive.models import LimitEvent, Project, Resource, ResourceUsability, Runner, Task, TaskStatus, Workspace
from hive.persistence.store import FileStore
from test_capacity_fallback import PREFERENCES


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/capacity_drill.py"


def drill_module():
    spec = importlib.util.spec_from_file_location("capacity_drill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed(tmp_path):
    state = tmp_path / "experiments/production-simulator"
    store = FileStore(state / "store")
    store.put(Workspace(id="default", name="lab", paused=True))
    project = store.put(Project(name="simulator", spec_repo="https://example.com/sim.git",
                                included_only=True, daily_budget_usd=0, agent_preferences=PREFERENCES))
    runner = store.put(Runner(name="lab", backends=["codex", "claude", "opencode"]))
    for backend in runner.backends:
        store.put(Resource(runner_id=runner.id, backend=backend, usability_status=ResourceUsability.usable))
    snapshot = {"project": {"project": project.model_dump()},
                "workspace": {"paused": True}, "storage": {"backend": "file", "store_path": str(state / "store")},
                "resources": {"runners": [runner.model_dump()],
                              "resources": [r.model_dump() for r in store.list(Resource)]}}
    return state, store, project, runner, snapshot


def test_offline_injection_selects_muse_and_restores_only_owned_cooldowns(tmp_path):
    """Real dispatch skips exact scoped synthetic limits, while restoration
    preserves subsequent provider telemetry and unrelated cooldown keys."""
    drill = drill_module()
    state, store, project, runner, snapshot = seed(tmp_path)
    manifest = drill.prepare(snapshot, "http://127.0.0.1:1", tmp_path / "evidence")
    assert all(not r.model_cooldowns for r in FileStore(state / "store").list(Resource))
    drill.apply(manifest, duration_s=3600)
    store = FileStore(state / "store")
    claude = next(r for r in store.list(Resource) if r.backend == "claude")
    injected = dict(claude.model_cooldowns)
    # A fresh healthy heartbeat must not erase the controlled scoped limits.
    from hive.runner.registration import RunnerRegister, register
    register(store, RunnerRegister(name=runner.name, backends=runner.backends, usage_snapshots={
        "claude": {"source": "oauth", "captured_at": time.time(), "windows": [
            {"kind": "weekly", "used_percent": 7, "resets_at": time.time()+7200}]}}), "default")
    claude = store.get(Resource, claude.id)
    assert claude.model_cooldowns == injected
    claude.model_cooldowns["other-model"] = time.time()+4000
    claude.total_tasks = 17
    store.put(claude)
    store.update(Workspace, "default", lambda w: setattr(w, "paused", False))
    task = store.put(Task(project_id=project.id, workstream_id="", repo=project.spec_repo, instructions="Implement the next item",
                          backend="codex", model=PREFERENCES[0].model))
    assert Supervisor(store, lambda *_: None).dispatch(project) == 1
    chosen = store.get(Task, task.id)
    assert chosen.backend == "opencode" and chosen.model == PREFERENCES[-1].model
    assert "gpt-6-astra" in chosen.dispatch_reason
    store.update(Task, task.id, lambda t: setattr(t, "status", TaskStatus.done))
    store.update(Workspace, "default", lambda w: setattr(w, "paused", True))
    drill.restore(manifest)
    restored = FileStore(state / "store").get(Resource, claude.id)
    assert set(restored.model_cooldowns) == {"other-model"}
    assert restored.total_tasks == 17 and restored.usage_source == "oauth"
    assert restored.usage_windows[0].used_percent == 7
    events = FileStore(state / "store").list(LimitEvent)
    assert any(e.kind == "synthetic_capacity" for e in events)
    assert not any(e.kind == "exhausted" for e in events)
    assert json.loads(manifest.read_text())["status"] == "restored"


def test_dry_run_matrix_distinguishes_model_and_shared_windows(tmp_path):
    """The real dispatcher in scratch memory keeps the other Claude model
    available for a scoped cap, but a shared cap moves directly to Muse."""
    drill = drill_module()
    _, _, _, _, snapshot = seed(tmp_path)
    manifest = drill.prepare(snapshot, "http://127.0.0.1:1", tmp_path / "evidence")
    matrix = json.loads((manifest.parent / "selection-matrix.json").read_text())
    assert matrix["label"].startswith("SYNTHETIC")
    rows = {row["scenario"]: row for row in matrix["scenarios"]}
    assert rows["baseline"]["model"] == PREFERENCES[0].model
    assert rows["codex_unavailable"]["model"] == PREFERENCES[1].model
    assert rows["first_claude_model_capped"]["model"] == PREFERENCES[2].model
    assert rows["second_claude_model_capped"]["model"] == PREFERENCES[1].model
    assert rows["claude_shared_cap"]["model"] == PREFERENCES[3].model
    assert rows["all_subscription_models_capped"]["model"] == PREFERENCES[3].model


@pytest.mark.parametrize("condition", ["unpaused", "running", "leader", "other_project"])
def test_apply_refuses_an_active_or_nonisolated_chief(tmp_path, condition):
    """Prepared evidence is not authorization to alter active or shared state."""
    drill = drill_module()
    state, store, project, _, snapshot = seed(tmp_path)
    manifest = drill.prepare(snapshot, "http://127.0.0.1:1", tmp_path / "evidence")
    if condition == "unpaused":
        store.update(Workspace, "default", lambda w: setattr(w, "paused", False))
    elif condition == "running":
        store.put(Task(project_id=project.id, workstream_id="", repo=project.spec_repo,
                       instructions="work", backend="codex", status=TaskStatus.running))
    elif condition == "leader":
        store.claim_leader("chief", 60)
    else:
        store.put(Project(name="unrelated"))
    before = {r.id: r.model_dump() for r in store.list(Resource)}
    with pytest.raises(RuntimeError):
        drill.apply(manifest)
    assert {r.id: r.model_dump() for r in FileStore(state / "store").list(Resource)} == before


def test_existing_longer_limit_and_later_quota_change_are_preserved(tmp_path):
    """The drill never shortens a real quota limit or overwrites a newer one
    during restoration, and reports conflicts for the operator to inspect."""
    drill = drill_module()
    state, store, _, _, snapshot = seed(tmp_path)
    codex = next(r for r in store.list(Resource) if r.backend == "codex")
    original = time.time()+10800
    codex.model_cooldowns[PREFERENCES[0].model] = original
    store.put(codex)
    snapshot["resources"]["resources"] = [r.model_dump() for r in store.list(Resource)]
    manifest = drill.prepare(snapshot, "http://127.0.0.1:1", tmp_path / "evidence")
    drill.apply(manifest, duration_s=60)
    store = FileStore(state / "store")
    assert store.get(Resource, codex.id).model_cooldowns[PREFERENCES[0].model] == original
    changed = original+600
    store.update(Resource, codex.id, lambda r: r.model_cooldowns.__setitem__(PREFERENCES[0].model, changed))
    result = drill.restore(manifest)
    assert result["status"] == "restored_with_conflicts"
    assert FileStore(state / "store").get(Resource, codex.id).model_cooldowns[PREFERENCES[0].model] == changed


def test_cli_prepare_reads_http_and_apply_requires_offline_chief(tmp_path):
    """The actual script captures a local API with GETs only, refuses a live
    listener, and can apply/recover from its durable manifest after shutdown."""
    state, store, project, _, snapshot = seed(tmp_path)
    requests = []

    class Chief(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            payload = {f"/api/projects/{project.id}": snapshot["project"],
                       "/api/resources": snapshot["resources"], "/api/workspace": snapshot["workspace"],
                       "/api/storage": snapshot["storage"], "/api/show": {"limits": []}}[self.path]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Chief)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "evidence"
    manifest = output / "manifest.json"

    def run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], text=True, capture_output=True, timeout=10)

    try:
        result = run("prepare", project.id, "--url", f"http://127.0.0.1:{server.server_port}", "--output", output)
        assert result.returncode == 0, result.stderr
        assert len(requests) == 5
        assert all(not r.model_cooldowns for r in FileStore(state / "store").list(Resource))
        result = run("apply", manifest, "--duration", "120")
        assert result.returncode != 0 and "stop the local chief" in result.stderr
        assert json.loads(manifest.read_text())["status"] == "prepared"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    result = run("apply", manifest, "--duration", "120")
    assert result.returncode == 0, result.stderr
    assert all(r.model_cooldowns for r in FileStore(state / "store").list(Resource) if r.backend != "opencode")
    assert run("restore", manifest).returncode == 0
    assert all(not r.model_cooldowns for r in FileStore(state / "store").list(Resource))
