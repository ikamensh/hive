"""Launchpad slices: Directive create/preview-routing, and Checkout reporting
from the runner heartbeat with canonical-repo drift surfacing.

See wiki/project-launchpad.md. The routing brain and sync action are stubbed;
these tests pin the real read/write paths that exist now."""

from fastapi.testclient import TestClient

from hive.config.settings import Config
from hive._control.supervisor import Supervisor
from hive.models import Machine, Resource, ResourceUsability
from hive.persistence.store import MemoryStore

RUNNER_HEADERS = {"X-Hive-Token": "t"}


def make_client(store):
    config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
                    orch_model="", runner_token="t", data_dir=None)
    from hive.api import create_app

    return TestClient(create_app(store, Supervisor(store, lambda p, e: None), config))


def make_project(client, **patch):
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    if patch:
        client.patch(f"/api/projects/{pid}", json=patch)
    return pid


def test_directive_persisted_and_in_project_payload():
    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client)

    created = client.post(f"/api/projects/{pid}/directives", json={"text": "set up CI"})
    assert created.status_code == 200
    body = created.json()
    assert body["text"] == "set up CI"
    # No online agent in this store -> stays triaging, with an honest note.
    assert body["status"] == "triaging"
    assert "wait" in body["routing_note"].lower()

    payload = client.get(f"/api/projects/{pid}").json()
    assert [d["id"] for d in payload["directives"]] == [body["id"]]


def test_directive_preview_routes_to_available_agent():
    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client)
    machine = store.put(Machine(name="MacBook"))
    store.put(Resource(machine_id=machine.id, runner_id="r1", backend="codex",
                        usability_status=ResourceUsability.usable, enabled=True))

    body = client.post(f"/api/projects/{pid}/directives", json={"text": "upgrade deps"}).json()
    assert body["status"] == "awaiting_executor"
    assert body["suggested_backend"] == "codex"
    assert body["suggested_machine_id"] == machine.id
    assert "MacBook" in body["routing_note"]


def test_empty_directive_rejected():
    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client)
    assert client.post(f"/api/projects/{pid}/directives", json={"text": "  "}).status_code == 400


def test_runner_checkout_report_surfaces_with_drift_via_canonical_match():
    store = MemoryStore()
    client = make_client(store)
    # Project stores the human-typed URL (no .git); runner reports the origin (.git).
    pid = make_project(client, spec_repo="https://github.com/ikamensh/hive")

    client.post(
        "/api/runners/register",
        headers=RUNNER_HEADERS,
        json={
            "name": "laptop",
            "backends": ["codex"],
            "machine_name": "MacBook",
            "checkouts": [
                {"repo": "git@github.com:ikamensh/hive.git", "exists": True,
                 "head_sha": "abc123", "branch": "main", "ahead": 2, "dirty": True},
            ],
        },
    )

    checkouts = client.get(f"/api/projects/{pid}").json()["checkouts"]
    assert len(checkouts) == 1
    c = checkouts[0]
    assert c["ahead"] == 2 and c["dirty"] is True  # drift signal present
    assert c["branch"] == "main"


def test_checkout_report_is_upserted_not_duplicated():
    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client, spec_repo="https://github.com/o/r")

    def report(ahead, dirty):
        client.post("/api/runners/register", headers=RUNNER_HEADERS, json={
            "name": "laptop", "backends": [], "machine_name": "MacBook",
            "checkouts": [{"repo": "https://github.com/o/r.git", "ahead": ahead, "dirty": dirty}],
        })

    report(1, True)
    report(0, False)  # later heartbeat: work was synced away

    checkouts = client.get(f"/api/projects/{pid}").json()["checkouts"]
    assert len(checkouts) == 1  # same (machine, repo) -> one record
    assert checkouts[0]["ahead"] == 0 and checkouts[0]["dirty"] is False
