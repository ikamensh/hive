"""Runner reboot semantics: in-flight tasks are requeued on boot registration,
left alone on heartbeat registration."""

from fastapi.testclient import TestClient

from hive.backends import PROBE_MARKER
from hive.config import Config
from hive.models import (
    Project,
    Resource,
    ResourceUsability,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
)
from hive.store import MemoryStore
from hive.supervisor import Supervisor

H = {"X-Hive-Token": "t"}


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
    ws = store.put(Workstream(project_id=project.id, title="w"))
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
