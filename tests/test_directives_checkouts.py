"""Launchpad slices: Directive intake (file-as-issue brain), and Checkout
reporting from the runner heartbeat with canonical-repo drift surfacing.

See wiki/project-launchpad.md. The full directive → resolve → review → landed
loop is covered in test_issues.py; here we pin the create-path contracts."""

from fastapi.testclient import TestClient

from hive.config.settings import Config
from hive._control.supervisor import Supervisor
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


def test_directive_without_repo_stays_triaging_with_reason():
    """A directive on an unconfigured project is never a silent dead end: it
    stays `triaging` and routing_note says exactly what is missing."""
    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client)

    created = client.post(f"/api/projects/{pid}/directives", json={"text": "set up CI"})
    assert created.status_code == 200
    body = created.json()
    assert body["text"] == "set up CI"
    assert body["status"] == "triaging"
    assert "configure a repo" in body["routing_note"]

    payload = client.get(f"/api/projects/{pid}").json()
    assert [d["id"] for d in payload["directives"]] == [body["id"]]


def test_directive_seeds_internal_work_item_no_github(monkeypatch):
    """The launchpad ask becomes a front-of-queue pipeline work item directly —
    no GitHub issue is filed (GitHub is a source of work in, never the ledger),
    and the resolve task starts with the ask inlined."""
    from hive.models import IssueItem, Task

    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client, spec_repo="https://github.com/o/r.git")

    body = client.post(
        f"/api/projects/{pid}/directives",
        json={"text": "Upgrade deps\n\nEverything minor, keep lockfile tidy."},
    ).json()

    assert body["status"] == "working"
    assert "agent started" in body["routing_note"]
    (item,) = store.list(IssueItem, project_id=pid)
    assert body["work_item_id"] == item.id
    assert item.external_ref["origin"] == "directive"
    assert item.issue_number == 0 and item.status == "resolving"
    (task,) = store.list(Task, project_id=pid)
    assert task.branch == f"hive/ask-{item.id[:8]}"
    assert "Upgrade deps" in task.instructions
    assert "NO GitHub issue" in task.instructions


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


def test_cli_ask_files_directive(monkeypatch):
    """`hive ask` is the CLI face of the launchpad box — full parity."""
    from hive.cli import build_parser, run

    store = MemoryStore()
    client = make_client(store)
    pid = make_project(client, spec_repo="https://github.com/o/r.git")
    out = run(build_parser().parse_args(["ask", pid, "Add a doctor command"]), client)

    assert out["status"] == "working" and out["work_item_id"]
