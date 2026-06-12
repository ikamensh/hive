"""Runner reboot semantics: in-flight tasks are requeued on boot registration,
left alone on heartbeat registration."""

from fastapi.testclient import TestClient

from hive.config import Config
from hive.models import Project, Task, TaskStatus, Workstream
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
