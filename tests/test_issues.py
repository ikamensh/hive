"""Issues mode: human-scanned ingestion and the deterministic per-issue pipeline
(resolve → review → land). The store pieces (reconcile, task setup, state) are
tested directly; the scan endpoint + resolve/review chaining + merge-on-accept
are exercised end-to-end through the API with GitHub mocked. The dormant ordered
variant (activate_next, orchestrator solve tools) keeps a couple of tests too."""

import pytest
from fastapi.testclient import TestClient

from hive.blobstore import LocalBlobStore
from hive.config import Config
from hive.issues import (
    activate_next,
    create_resolve_tasks,
    issue_branch,
    reconcile,
)
from hive.models import (
    Project,
    ProjectState,
    Task,
    TaskKind,
    Verdict,
    WorkSource,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
    parse_resolve,
    parse_review,
)
from hive.orchestrator import Tools
from hive.store import MemoryStore
from hive.supervisor import Supervisor, compute_state
from tests.test_api_e2e import RUNNER_HEADERS, _pump, _register_usable_runner


def issue(number, title="t", body="b", attachments=None):
    return {
        "number": number,
        "title": title,
        "url": f"u/{number}",
        "doc": f"# {title}\n\n{body}",
        "attachments": attachments or [],
    }


def issues_project(store) -> Project:
    return store.put(
        Project(name="p", spec_repo="https://github.com/o/r.git", work_source=WorkSource.issues)
    )


# -- ingestion (pure) --------------------------------------------------------


def test_reconcile_ingests_as_resolving():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(7), issue(3, attachments=["http://x/a.png"])])
    ws = {w.issue_number: w for w in store.list(Workstream, project_id=project.id)}
    assert {w.status for w in ws.values()} == {WorkstreamStatus.resolving}
    assert all(w.source == WorkstreamSource.issue for w in ws.values())
    assert ws[3].issue_attachments == ["http://x/a.png"]


def test_reconcile_idempotent_and_cancels_closed():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1), issue(2)])
    reconcile(store, project, [issue(1)])  # #2 closed on GitHub
    ws = {w.issue_number: w for w in store.list(Workstream, project_id=project.id)}
    assert len(ws) == 2  # no duplicates
    assert ws[2].status == WorkstreamStatus.cancelled


@pytest.mark.parametrize("stuck", [WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected])
def test_reconcile_regates_stuck_issue_on_rescan(stuck):
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1)])
    ws = store.list(Workstream, project_id=project.id)[0]
    ws.status = stuck
    store.put(ws)
    reconcile(store, project, [issue(1)])  # human acted, scans again
    assert store.get(Workstream, ws.id).status == WorkstreamStatus.resolving


# -- resolve task setup ------------------------------------------------------


def test_create_resolve_tasks_one_per_issue_idempotent():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1), issue(2)])
    assert create_resolve_tasks(store, project) == 2
    tasks = store.list(Task, project_id=project.id)
    assert len(tasks) == 2
    assert all(t.kind == TaskKind.resolve and t.backend == "codex" for t in tasks)
    # a second call doesn't double up while the first tasks are pending
    assert create_resolve_tasks(store, project) == 0


def test_resolve_task_carries_issue_context():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(42, "login broken", "stack trace here", ["http://x/s.png"])])
    create_resolve_tasks(store, project)
    task = store.list(Task, project_id=project.id)[0]
    assert task.branch == issue_branch(42) == "hive/issue-42"
    assert task.issue_number == 42
    assert "stack trace here" in task.issue_doc
    assert task.issue_attachments == ["http://x/s.png"]
    assert "#42" in task.instructions and "ISSUE.md" in task.instructions


def test_parse_resolve_and_review():
    assert parse_resolve("done\nOUTCOME: FIXED") == Verdict.accept
    assert parse_resolve("stop\nOUTCOME: BLOCKED") == Verdict.reject
    assert parse_resolve("nothing") == Verdict.none
    assert parse_review("ok\nREVIEW: ACCEPT") == Verdict.accept
    assert parse_review("bad\nREVIEW: REJECT") == Verdict.reject


# -- supervisor state --------------------------------------------------------


def _ws(p, status):
    return Workstream(project_id=p.id, title="x", status=status, source=WorkstreamSource.issue)


def _task(p, backend="codex"):
    return Task(project_id=p.id, workstream_id="w", repo="r", instructions="x",
                kind=TaskKind.resolve, backend=backend)


def test_compute_state_issues_mode():
    p = Project(name="p", spec_repo="x", work_source=WorkSource.issues)
    # a pending resolve/review task whose backend is available → working
    assert compute_state(p, [_ws(p, WorkstreamStatus.resolving)], 0, [_task(p)], {"codex"}) == ProjectState.working
    # open issue, no task in flight → waiting on a human
    assert compute_state(p, [_ws(p, WorkstreamStatus.blocked_clarity)], 0, [], set()) == ProjectState.blocked_clarity
    assert compute_state(p, [_ws(p, WorkstreamStatus.rejected)], 0, [], set()) == ProjectState.blocked_clarity
    # drained queue
    assert compute_state(p, [_ws(p, WorkstreamStatus.done)], 0, [], set()) == ProjectState.idle_no_open_issues
    assert compute_state(p, [_ws(p, WorkstreamStatus.cancelled)], 0, [], set()) == ProjectState.idle_no_open_issues


# -- dormant ordered variant -------------------------------------------------


def test_activate_next_promotes_lowest_queued_when_idle():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1), issue(2)])
    assert activate_next(store, project) is None  # nothing queued yet
    for w in store.list(Workstream, project_id=project.id):
        w.status = WorkstreamStatus.queued
        w.order = w.issue_number
        store.put(w)
    activated = activate_next(store, project)
    assert activated.issue_number == 1 and activated.status == WorkstreamStatus.active


def test_create_task_rejected_for_non_active_issue():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1)])  # status resolving, not active
    ws = store.list(Workstream, project_id=project.id)[0]
    out = Tools(store, project, spec=None).create_task(ws.id, "r", "do it", backend="cursor")
    assert "one issue at a time" in out
    assert not store.list(Task, project_id=project.id)


def test_tool_surface_differs_by_work_source():
    store = MemoryStore()
    issues = {f.__name__ for f in Tools(store, issues_project(store), spec=None).functions()}
    spec = {f.__name__ for f in Tools(store, store.put(Project(name="s", spec_repo="x")), spec=None).functions()}
    assert {"order_issues", "resolve_issue"} <= issues
    assert {"create_workstream", "complete_workstream", "mark_goal_complete"}.isdisjoint(issues)
    assert {"create_workstream", "complete_workstream", "mark_goal_complete"} <= spec


# -- end to end: scan → resolve → review → land ------------------------------


@pytest.fixture
def app(tmp_path):
    store = MemoryStore()
    supervisor = Supervisor(store, lambda pid, events: None)
    config = Config(gcp_project="", gcs_bucket="", gh_token="t", gemini_api_key="",
                    orch_model="", runner_token="test-token", data_dir=tmp_path)
    from hive.api import create_app

    return TestClient(create_app(store, supervisor, config, blobs=LocalBlobStore(tmp_path / "blobs"))), store


def _issues_project_via_api(client):
    pid = client.post("/api/projects", json={"name": "iss"}).json()["id"]
    client.patch(f"/api/projects/{pid}",
                 json={"spec_repo": "https://github.com/o/r.git", "work_source": "issues"})
    return pid


def _poll(client, rid):
    return client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]


def _report(client, task_id, text, is_error=False):
    client.post(f"/api/tasks/{task_id}/result",
                json={"text": text, "is_error": is_error}, headers=RUNNER_HEADERS)


def _pass_preflight(monkeypatch):
    """Scan runs the control-plane preflight, which hits GitHub for repo perms.
    Stub it green so the scan flow tests stay offline (preflight has its own tests)."""
    monkeypatch.setattr("hive.api.preflight_checks", lambda store, config, project: [])


def test_scan_resolve_review_accept_lands(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(1, "bug")])

    resp = client.post(f"/api/projects/{pid}/scan-issues").json()
    assert resp["open_issues"] == 1 and resp["resolve_queued"] == 1

    _pump(client, store)
    resolve = _poll(client, rid)
    assert resolve["kind"] == "resolve" and resolve["branch"] == "hive/issue-1"
    _report(client, resolve["id"], "fixed it\nOUTCOME: FIXED")

    ws_id = resolve["workstream_id"]
    assert store.get(Workstream, ws_id).status == WorkstreamStatus.reviewing

    _pump(client, store)
    review = _poll(client, rid)
    assert review["kind"] == "review" and review["branch"] == "hive/issue-1"

    merged = {}
    monkeypatch.setattr("hive.api.merge_branch",
                        lambda repo, head, token, message="": merged.setdefault("head", head))
    monkeypatch.setattr("hive.api.resolve_issue_on_github",
                        lambda repo, number, comment, token: merged.setdefault("closed", number))
    _report(client, review["id"], "looks good\nREVIEW: ACCEPT")

    assert merged == {"head": "hive/issue-1", "closed": 1}
    assert store.get(Workstream, ws_id).status == WorkstreamStatus.done


def test_resolve_blocked_holds_issue(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(5, "vague feature")])
    client.post(f"/api/projects/{pid}/scan-issues")

    _pump(client, store)
    resolve = _poll(client, rid)
    _report(client, resolve["id"], "needs product decisions\nOUTCOME: BLOCKED")
    ws = store.get(Workstream, resolve["workstream_id"])
    assert ws.status == WorkstreamStatus.blocked_clarity
    # no review task was queued
    assert not [t for t in store.list(Task, project_id=pid) if t.kind == TaskKind.review]


def test_review_reject_marks_rejected(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(8, "bug")])
    client.post(f"/api/projects/{pid}/scan-issues")
    _pump(client, store)
    resolve = _poll(client, rid)
    _report(client, resolve["id"], "fixed\nOUTCOME: FIXED")
    _pump(client, store)
    review = _poll(client, rid)
    _report(client, review["id"], "broke other things\nREVIEW: REJECT")
    assert store.get(Workstream, review["workstream_id"]).status == WorkstreamStatus.rejected


def test_scan_downloads_attachments_and_serves_to_runner(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr(
        "hive.api.fetch_open_issues_full",
        lambda repo, token: [issue(3, "bug", attachments=["https://github.com/user-attachments/assets/a.png"])],
    )

    class FakeResp:
        content = b"PNGBYTES"

        def raise_for_status(self):
            pass

    monkeypatch.setattr("hive.issues.httpx.get", lambda *a, **k: FakeResp())
    client.post(f"/api/projects/{pid}/scan-issues")

    task = store.list(Task, project_id=pid)[0]
    assert task.issue_attachments == ["a.png"]  # URL replaced by stored filename
    got = client.get(f"/api/tasks/{task.id}/attachments/a.png", headers=RUNNER_HEADERS)
    assert got.status_code == 200 and got.content == b"PNGBYTES"


def test_scan_rejected_when_not_issues_mode(app):
    client, _ = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    assert client.post(f"/api/projects/{project['id']}/scan-issues").status_code == 400


# -- preflight ---------------------------------------------------------------


def _usable_codex(store, project):
    from hive.models import Resource, ResourceUsability, Runner

    runner = store.put(Runner(workspace_id=project.workspace_id, name="cx", backends=["codex"]))
    store.put(Resource(workspace_id=project.workspace_id, runner_id=runner.id, backend="codex",
                       usability_status=ResourceUsability.usable))


def test_preflight_checks(monkeypatch):
    from hive.preflight import preflight_checks

    cfg = Config(gcp_project="", gcs_bucket="", gh_token="t", gemini_api_key="",
                 orch_model="", runner_token="x", data_dir=".")
    store = MemoryStore()

    spec = store.put(Project(name="s", spec_repo="https://github.com/o/r.git"))
    by_name = {c.name: c for c in preflight_checks(store, cfg, spec)}
    assert not by_name["issues_mode"].ok  # spec mode → hard fail

    project = issues_project(store)
    _usable_codex(store, project)
    monkeypatch.setattr("hive.preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": True, "has_issues": True, "default_branch": "main"})
    checks = {c.name: c for c in preflight_checks(store, cfg, project)}
    assert all(c.ok for c in checks.values() if c.hard)
    assert checks["repo_write_access"].ok and checks["codex_runner_usable"].ok

    monkeypatch.setattr("hive.preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": False, "has_issues": True, "default_branch": "main"})
    checks = {c.name: c for c in preflight_checks(store, cfg, project)}
    assert not checks["repo_write_access"].ok  # read-only token → hard fail


def test_preflight_endpoint_queues_runner_check(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    monkeypatch.setattr("hive.preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": True, "has_issues": True, "default_branch": "main"})

    resp = client.post(f"/api/projects/{pid}/issues-preflight").json()
    assert resp["ok"] is True
    assert resp["runner_check_task"]
    assert all(c["ok"] for c in resp["checks"] if c["hard"])

    _pump(client, store)
    task = _poll(client, rid)
    assert task["kind"] == "preflight"


def test_scan_blocked_by_failing_preflight(app, monkeypatch):
    client, _ = app
    pid = _issues_project_via_api(client)
    monkeypatch.setattr("hive.preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": False, "has_issues": True, "default_branch": "main"})
    resp = client.post(f"/api/projects/{pid}/scan-issues")
    assert resp.status_code == 409
    assert any(c["name"] == "repo_write_access" for c in resp.json()["detail"]["checks"])
