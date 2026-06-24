"""Issue solving: human-scanned ingestion and deterministic resolve/review/land.

The store pieces (reconcile, task setup, state) are tested directly; the scan
endpoint plus resolve/review chaining and merge-on-accept are exercised
end-to-end through the API with GitHub mocked.
"""

import pytest
from fastapi.testclient import TestClient

from hive.persistence.blobstore import LocalBlobStore
from hive.config.settings import Config
from hive.workstreams._issues import (
    activate_next,
    advance_issues,
    delete_branch,
    ensure_issue_workstream,
    issue_branch,
    LANDING_FAILED_PREFIX,
    MergeConflictError,
    project_workstreams,
    reconcile,
    resolve_issue_on_github,
)
from hive.models import (
    Project,
    ProjectState,
    HumanTask,
    HumanTaskStatus,
    IssueRun,
    IssueRunScope,
    IssueRunStatus,
    ProjectWorkstreamKind,
    Task,
    TaskKind,
    TaskStatus,
    Verdict,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
    parse_resolve,
    parse_review,
)
from hive.control._orchestrator import Tools
from hive.persistence.store import MemoryStore
from hive.control._supervisor import Supervisor, compute_state
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
        Project(name="p", spec_repo="https://github.com/o/r.git")
    )


# -- ingestion (pure) --------------------------------------------------------


def test_reconcile_ingests_as_queued():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(7), issue(3, attachments=["http://x/a.png"])])
    ws = {w.issue_number: w for w in store.list(Workstream, project_id=project.id)}
    assert {w.status for w in ws.values()} == {WorkstreamStatus.queued}
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


@pytest.mark.parametrize(
    "stuck",
    [WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected, WorkstreamStatus.resolving],
)
def test_reconcile_regates_stuck_issue_on_rescan(stuck):
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1)])
    ws = store.list(Workstream, project_id=project.id)[0]
    ws.status = stuck  # blocked/rejected, or errored mid-flight with no live task
    store.put(ws)
    reconcile(store, project, [issue(1)])  # human acted, scans again
    assert store.get(Workstream, ws.id).status == WorkstreamStatus.queued


def test_reconcile_closed_landing_failure_is_done():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(4)])
    ws = store.list(Workstream, project_id=project.id)[0]
    ws.status = WorkstreamStatus.rejected
    ws.parked_reason = f"{LANDING_FAILED_PREFIX}: close issue #4 failed"
    store.put(ws)

    notes = reconcile(store, project, [])  # issue closed on GitHub

    assert store.get(Workstream, ws.id).status == WorkstreamStatus.done
    assert notes == ["marked #4 done: issue closed on GitHub after landing retry"]


# -- resolve task setup ------------------------------------------------------


def test_advance_issues_starts_one_at_a_time():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(2), issue(1)])
    # strict: one issue promoted to resolving with a resolve task; the rest wait
    assert advance_issues(store, project) == 1
    tasks = store.list(Task, project_id=project.id)
    assert len(tasks) == 1 and tasks[0].kind == TaskKind.resolve and tasks[0].backend == "codex"
    assert tasks[0].issue_number == 1  # lowest order first
    statuses = {w.issue_number: w.status for w in store.list(Workstream, project_id=project.id)}
    assert statuses == {1: WorkstreamStatus.resolving, 2: WorkstreamStatus.queued}
    # a second call is a no-op while issue #1 is in flight
    assert advance_issues(store, project) == 0
    assert len(store.list(Task, project_id=project.id)) == 1


def test_selected_issue_run_starts_only_selected_issue():
    store = MemoryStore()
    project = issues_project(store)
    stream = ensure_issue_workstream(store, project)
    reconcile(store, project, [issue(1), issue(2)], workstream=stream)
    run = store.put(
        IssueRun(
            project_id=project.id,
            workstream_id=stream.id,
            repo=stream.repo,
            scope=IssueRunScope.selected,
            issue_numbers=[2],
        )
    )

    assert advance_issues(store, project, workstream=stream, run=run) == 1

    task = store.list(Task, project_id=project.id)[0]
    assert task.issue_number == 2
    assert task.run_id == run.id
    assert task.work_item_id == task.workstream_id
    statuses = {w.issue_number: w.status for w in store.list(Workstream, project_id=project.id)}
    assert statuses == {1: WorkstreamStatus.queued, 2: WorkstreamStatus.resolving}
    assert store.get(IssueRun, run.id).status == IssueRunStatus.running


def test_resolve_task_carries_issue_context():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(42, "login broken", "stack trace here", ["http://x/s.png"])])
    advance_issues(store, project, model="operator-model")
    task = store.list(Task, project_id=project.id)[0]
    assert task.branch == issue_branch(42) == "hive/issue-42"
    assert task.fresh_branch is True
    assert task.model == "operator-model"
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


def test_resolve_issue_close_is_idempotent_when_already_closed(monkeypatch):
    class Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        @property
        def is_success(self):
            return 200 <= self.status_code < 300

        def json(self):
            return self._payload

        def raise_for_status(self):
            if not self.is_success:
                raise AssertionError("unexpected raise_for_status")

    calls = []
    monkeypatch.setattr("hive.workstreams._issues.httpx.post", lambda *a, **k: Resp(201, {}))
    monkeypatch.setattr(
        "hive.workstreams._issues.httpx.patch",
        lambda *a, **k: calls.append("patch") or Resp(422, {"message": "Validation Failed"}),
    )
    monkeypatch.setattr("hive.workstreams._issues.httpx.get", lambda *a, **k: Resp(200, {"state": "closed"}))

    resolve_issue_on_github("https://github.com/o/r", 4, "done", "token")

    assert calls == ["patch"]


class _DeleteResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"message": self.text}


def test_delete_branch_is_idempotent_when_branch_already_gone(monkeypatch):
    # Landing already merged+closed the issue; a 404 on the branch must not raise.
    seen = []
    monkeypatch.setattr(
        "hive.workstreams._issues.httpx.delete",
        lambda url, **k: seen.append(url) or _DeleteResp(404, "Not Found"),
    )
    delete_branch("https://github.com/o/r", "hive/issue-9", "token")
    assert seen[0].endswith("/repos/o/r/git/refs/heads/hive/issue-9")


def test_delete_branch_raises_on_unexpected_status(monkeypatch):
    monkeypatch.setattr(
        "hive.workstreams._issues.httpx.delete", lambda *a, **k: _DeleteResp(500, "server error")
    )
    with pytest.raises(RuntimeError, match="delete branch hive/issue-9"):
        delete_branch("https://github.com/o/r", "hive/issue-9", "token")


# -- supervisor state --------------------------------------------------------


def _ws(p, status):
    return Workstream(project_id=p.id, title="x", status=status, source=WorkstreamSource.issue)


def _task(p, backend="codex"):
    return Task(project_id=p.id, workstream_id="w", repo="r", instructions="x",
                kind=TaskKind.resolve, backend=backend)


def test_compute_state_issue_items_are_project_attention():
    p = Project(name="p", spec_repo="x")
    # a pending resolve/review task whose backend is available → working
    assert compute_state(p, [_ws(p, WorkstreamStatus.resolving)], 0, [_task(p)], {"codex"}) == ProjectState.working
    # open issue, no task in flight → waiting on a human
    assert compute_state(p, [_ws(p, WorkstreamStatus.blocked_clarity)], 0, [], set()) == ProjectState.needs_attention
    assert compute_state(p, [_ws(p, WorkstreamStatus.rejected)], 0, [], set()) == ProjectState.needs_attention
    # drained issue work does not change the project kind
    assert compute_state(p, [_ws(p, WorkstreamStatus.done)], 0, [], set()) == ProjectState.idle
    assert compute_state(p, [_ws(p, WorkstreamStatus.cancelled)], 0, [], set()) == ProjectState.idle


# -- dormant ordered variant -------------------------------------------------


def test_activate_next_promotes_lowest_queued_when_idle():
    store = MemoryStore()
    project = issues_project(store)
    assert activate_next(store, project) is None  # nothing queued yet
    for n in (2, 1):
        store.put(Workstream(project_id=project.id, title=f"#{n}", status=WorkstreamStatus.queued,
                             source=WorkstreamSource.issue, issue_number=n, order=n))
    activated = activate_next(store, project)
    assert activated.issue_number == 1 and activated.status == WorkstreamStatus.active


def test_create_task_rejected_for_non_active_issue():
    store = MemoryStore()
    project = issues_project(store)
    reconcile(store, project, [issue(1)])  # status resolving, not active
    ws = store.list(Workstream, project_id=project.id)[0]
    out = Tools(store, project, spec=None).create_task(ws.id, "r", "do it", backend="cursor")
    assert "deterministic issue pipeline" in out
    assert not store.list(Task, project_id=project.id)


def test_planner_tool_surface_is_single_project_surface():
    store = MemoryStore()
    tools = {f.__name__ for f in Tools(store, issues_project(store), spec=None).functions()}
    assert {"create_workstream", "complete_workstream", "mark_goal_complete"} <= tools
    assert {"order_issues", "resolve_issue"}.isdisjoint(tools)


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
                 json={"spec_repo": "https://github.com/o/r.git"})
    return pid


def _poll(client, rid):
    return client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]


def _report(client, task_id, text, is_error=False, structured_result=None):
    body = {"text": text, "is_error": is_error}
    if structured_result is not None:
        body["structured_result"] = structured_result
    client.post(f"/api/tasks/{task_id}/result", json=body, headers=RUNNER_HEADERS)


def _pass_preflight(monkeypatch):
    """Scan runs the chief preflight, which hits GitHub for repo perms.
    Stub it green so the scan flow tests stay offline (preflight has its own tests)."""
    monkeypatch.setattr("hive.api.preflight_checks", lambda store, config, project: [])


def test_resolve_auth_block_stops_dispatch_and_files_todo(app, monkeypatch):
    """A billing/login block on a real resolve task (not just a probe) must mark
    the backend unusable so dispatch stops, and file an operator todo — instead
    of silently re-dispatching every issue onto a dead credential forever.

    The runner classifies the failure (unit-tested in test_runner_quota); this
    asserts the chief acts on the auth_blocked flag for non-probe work."""
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(1, "bug")])

    client.post(f"/api/projects/{pid}/scan-issues")
    _pump(client, store)
    resolve = _poll(client, rid)
    assert resolve["kind"] == "resolve"

    client.post(
        f"/api/tasks/{resolve['id']}/result",
        json={
            "text": "codex: Subscription/billing issue — check your account status.",
            "is_error": True,
            "auth_blocked": True,
        },
        headers=RUNNER_HEADERS,
    )

    codex = next(r for r in client.get("/api/resources").json()["resources"]
                 if r["backend"] == "codex")
    assert codex["usability_status"] == "failed"

    open_todos = [t for t in store.list(HumanTask) if t.status == HumanTaskStatus.open]
    assert any("Fix codex login on codex-runner" in t.title for t in open_todos)


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
    _report(
        client,
        resolve["id"],
        "Found the close comment was hardcoded to the merge notice.\n"
        "Updated the landing path to include the resolver's summary.",
        structured_result={
            "task_id": resolve["id"],
            "outcome": "fixed",
            "tests_run": ["pytest"],
            "branch_pushed": True,
        },
    )

    ws_id = resolve["workstream_id"]
    assert store.get(Workstream, ws_id).status == WorkstreamStatus.reviewing

    _pump(client, store)
    review = _poll(client, rid)
    assert review["kind"] == "review" and review["branch"] == "hive/issue-1"

    merged = {}
    monkeypatch.setattr("hive.api.merge_branch",
                        lambda repo, head, token, message="": merged.setdefault("head", head))
    def close_issue(repo, number, comment, token):
        merged["closed"] = number
        merged["comment"] = comment

    monkeypatch.setattr("hive.api.resolve_issue_on_github", close_issue)
    monkeypatch.setattr("hive.api.delete_branch",
                        lambda repo, branch, token: merged.setdefault("deleted", branch))
    _report(
        client,
        review["id"],
        "Verified the comment includes the fix report.",
        structured_result={
            "task_id": review["id"],
            "outcome": "accept",
            "tests_run": ["pytest"],
            "changes_pushed": False,
        },
    )

    assert merged["head"] == "hive/issue-1"
    assert merged["closed"] == 1
    assert "Resolved by Hive — merged `hive/issue-1`" in merged["comment"]
    assert "### Fix summary" in merged["comment"]
    assert "Found the close comment was hardcoded" in merged["comment"]
    assert "### Review summary" in merged["comment"]
    assert "Verified the comment includes the fix report." in merged["comment"]
    assert "OUTCOME:" not in merged["comment"]
    assert "REVIEW:" not in merged["comment"]
    assert store.get(Workstream, ws_id).status == WorkstreamStatus.done
    # The merged work branch is cleaned up so issue branches don't pile up.
    assert merged["deleted"] == "hive/issue-1"


def test_strict_sequencing_starts_next_issue_only_after_landing(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full",
                        lambda repo, token: [issue(1, "bug a"), issue(2, "bug b")])
    monkeypatch.setattr("hive.api.merge_branch", lambda repo, head, token, message="": None)
    monkeypatch.setattr("hive.api.resolve_issue_on_github", lambda repo, number, comment, token: None)

    resp = client.post(f"/api/projects/{pid}/scan-issues").json()
    assert resp["open_issues"] == 2 and resp["resolve_queued"] == 1  # only one started

    def resolve_numbers():
        return sorted(t.issue_number for t in store.list(Task, project_id=pid) if t.kind == TaskKind.resolve)

    assert resolve_numbers() == [1]  # issue #2 is still queued, no task yet

    # drive issue #1 all the way through resolve → review → land
    _pump(client, store)
    r1 = _poll(client, rid)
    assert r1["issue_number"] == 1
    _report(client, r1["id"], "fixed\nOUTCOME: FIXED")
    _pump(client, store)
    rev1 = _poll(client, rid)
    assert rev1["kind"] == "review" and rev1["issue_number"] == 1
    assert resolve_numbers() == [1]  # #2 still hasn't started while #1 is reviewing
    _report(client, rev1["id"], "good\nREVIEW: ACCEPT")

    # only now does issue #2 begin
    assert store.get(Workstream, rev1["workstream_id"]).status == WorkstreamStatus.done
    assert resolve_numbers() == [1, 2]
    _pump(client, store)
    r2 = _poll(client, rid)
    assert r2["kind"] == "resolve" and r2["issue_number"] == 2


def test_landing_failure_does_not_advance_to_next_issue(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr(
        "hive.api.fetch_open_issues_full",
        lambda repo, token: [issue(1, "bug a"), issue(2, "bug b")],
    )
    def fail_merge(repo, head, token, message=""):
        raise RuntimeError("boom")

    monkeypatch.setattr("hive.api.merge_branch", fail_merge)

    client.post(f"/api/projects/{pid}/scan-issues")
    _pump(client, store)
    resolve = _poll(client, rid)
    _report(client, resolve["id"], "fixed\nOUTCOME: FIXED")
    _pump(client, store)
    review = _poll(client, rid)
    _report(client, review["id"], "good\nREVIEW: ACCEPT")

    tasks = store.list(Task, project_id=pid)
    assert sorted(t.issue_number for t in tasks if t.kind == TaskKind.resolve) == [1]
    ws = store.get(Workstream, review["workstream_id"])
    assert ws.status == WorkstreamStatus.rejected
    assert ws.parked_reason.startswith(LANDING_FAILED_PREFIX)
    assert [t.title for t in store.list(HumanTask, project_id=pid)] == ["Land issue #1 failed"]


def test_landing_merge_conflict_queues_ai_integration_review(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr(
        "hive.api.fetch_open_issues_full",
        lambda repo, token: [issue(1, "bug a"), issue(2, "bug b")],
    )
    merge_calls = []

    def merge(repo, head, token, message=""):
        merge_calls.append(head)
        if len(merge_calls) == 1:
            raise MergeConflictError(head, "main")

    closed = {}
    monkeypatch.setattr("hive.api.merge_branch", merge)
    monkeypatch.setattr(
        "hive.api.resolve_issue_on_github",
        lambda repo, number, comment, token: closed.setdefault("number", number),
    )

    client.post(f"/api/projects/{pid}/scan-issues")
    _pump(client, store)
    resolve = _poll(client, rid)
    _report(client, resolve["id"], "fixed\nOUTCOME: FIXED")
    _pump(client, store)
    review = _poll(client, rid)
    _report(client, review["id"], "good\nREVIEW: ACCEPT")

    ws = store.get(Workstream, review["workstream_id"])
    assert ws.status == WorkstreamStatus.reviewing
    assert not store.list(HumanTask, project_id=pid)
    reviews = [t for t in store.list(Task, project_id=pid) if t.kind == TaskKind.review]
    integration = next(t for t in reviews if t.id != review["id"])
    assert "landing_integration" in integration.prompt_versions
    assert "could not merge the issue branch" in integration.instructions
    assert sorted(t.issue_number for t in store.list(Task, project_id=pid) if t.kind == TaskKind.resolve) == [1]

    _pump(client, store)
    repair = _poll(client, rid)
    assert repair["id"] == integration.id
    _report(client, repair["id"], "Merged latest main and tests pass.\nREVIEW: ACCEPT")

    assert merge_calls == ["hive/issue-1", "hive/issue-1"]
    assert closed["number"] == 1
    assert store.get(Workstream, review["workstream_id"]).status == WorkstreamStatus.done
    assert sorted(t.issue_number for t in store.list(Task, project_id=pid) if t.kind == TaskKind.resolve) == [1, 2]


def test_landing_integration_reject_creates_human_todo(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(1, "bug")])
    monkeypatch.setattr(
        "hive.api.merge_branch",
        lambda repo, head, token, message="": (_ for _ in ()).throw(MergeConflictError(head, "main")),
    )

    client.post(f"/api/projects/{pid}/scan-issues")
    _pump(client, store)
    resolve = _poll(client, rid)
    _report(client, resolve["id"], "fixed\nOUTCOME: FIXED")
    _pump(client, store)
    review = _poll(client, rid)
    _report(client, review["id"], "good\nREVIEW: ACCEPT")
    _pump(client, store)
    repair = _poll(client, rid)

    _report(client, repair["id"], "Need a product decision about which behavior wins.\nREVIEW: REJECT")

    ws = store.get(Workstream, repair["workstream_id"])
    assert ws.status == WorkstreamStatus.rejected
    assert ws.parked_reason == f"{LANDING_FAILED_PREFIX}: integration needs human input"
    todos = store.list(HumanTask, project_id=pid)
    assert [t.title for t in todos] == ["Land issue #1 failed"]
    assert "Need a product decision" in todos[0].instructions


def test_mark_landing_failure_todo_done_marks_closed_issue_done(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.issue_is_closed", lambda repo, number, token: True)
    project = store.get(Project, pid)
    ws = store.put(
        Workstream(
            workspace_id=project.workspace_id,
            project_id=pid,
            title="#4 settings",
            status=WorkstreamStatus.rejected,
            source=WorkstreamSource.issue,
            issue_number=4,
            parked_reason=f"{LANDING_FAILED_PREFIX}: close failed",
        )
    )
    human = store.put(
        HumanTask(
            workspace_id=project.workspace_id,
            project_id=pid,
            title="Land issue #4 failed",
            instructions="land it",
        )
    )

    resp = client.post(f"/api/human-tasks/{human.id}/done")

    assert resp.status_code == 200
    assert store.get(HumanTask, human.id).status == HumanTaskStatus.done
    assert store.get(Workstream, ws.id).status == WorkstreamStatus.done


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


def test_cancel_pending_issue_task_requeues_issue(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(6, "bug")])
    client.post(f"/api/projects/{pid}/scan-issues")

    task = store.list(Task, project_id=pid)[0]
    resp = client.post(f"/api/tasks/{task.id}/cancel")
    assert resp.status_code == 200
    assert store.get(Task, task.id).status == TaskStatus.cancelled
    ws = store.get(Workstream, task.workstream_id)
    assert ws.status == WorkstreamStatus.queued
    assert "scan to retry" in ws.parked_reason


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

    monkeypatch.setattr("hive.workstreams._issues.httpx.get", lambda *a, **k: FakeResp())
    resp = client.post(f"/api/projects/{pid}/scan-issues").json()

    task = store.list(Task, project_id=pid)[0]
    assert resp["attachments_downloaded"] == 1
    assert resp["attachments_failed"] == 0
    assert task.issue_attachments == ["a.png"]  # URL replaced by stored filename
    got = client.get(f"/api/tasks/{task.id}/attachments/a.png", headers=RUNNER_HEADERS)
    assert got.status_code == 200 and got.content == b"PNGBYTES"


def test_scan_allowed_on_normal_project(app, monkeypatch):
    client, store = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(9, "normal project bug")])

    resp = client.post(f"/api/projects/{project['id']}/scan-issues")

    assert resp.status_code == 200
    assert resp.json()["open_issues"] == 1
    ws = store.list(Workstream, project_id=project["id"])[0]
    assert ws.source == WorkstreamSource.issue and ws.issue_number == 9


def test_project_workstreams_attach_legacy_work_items():
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://github.com/o/r.git"))
    manual = store.put(Workstream(project_id=project.id, title="manual"))
    issue_item = store.put(
        Workstream(
            project_id=project.id,
            title="#1 bug",
            source=WorkstreamSource.issue,
            issue_number=1,
        )
    )

    streams = project_workstreams(store, project)
    iteration = next(w for w in streams if w.kind == ProjectWorkstreamKind.iteration)
    github = next(w for w in streams if w.kind == ProjectWorkstreamKind.github_issues)

    assert store.get(Workstream, manual.id).workstream_id == iteration.id
    saved_issue = store.get(Workstream, issue_item.id)
    assert saved_issue.workstream_id == github.id
    assert saved_issue.repo == project.spec_repo


def test_issue_workstream_run_selected_endpoint(app, monkeypatch):
    client, store = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    _pass_preflight(monkeypatch)
    monkeypatch.setattr(
        "hive.api.fetch_open_issues_full",
        lambda repo, token: [issue(1, "skip"), issue(2, "run me")],
    )

    detail = client.get(f"/api/projects/{project['id']}").json()
    stream = next(w for w in detail["workstreams"] if w["kind"] == "github_issues")
    sync = client.post(f"/api/projects/{project['id']}/workstreams/{stream['id']}/sync").json()
    assert sync["open_issues"] == 2 and sync["resolve_queued"] == 0

    resp = client.post(
        f"/api/projects/{project['id']}/workstreams/{stream['id']}/issue-runs",
        json={"scope": "selected", "issue_numbers": [2]},
    ).json()

    assert resp["run"]["issue_numbers"] == [2]
    assert resp["resolve_queued"] == 1
    task = store.list(Task, project_id=project["id"])[0]
    assert task.issue_number == 2 and task.run_id == resp["run"]["id"]


def test_issue_workstream_can_be_disabled(app, monkeypatch):
    client, store = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    _pass_preflight(monkeypatch)
    monkeypatch.setattr("hive.api.fetch_open_issues_full", lambda repo, token: [issue(1, "bug")])

    stream = next(
        w for w in client.get(f"/api/projects/{project['id']}").json()["workstreams"]
        if w["kind"] == "github_issues"
    )
    disabled = client.patch(
        f"/api/projects/{project['id']}/workstreams/{stream['id']}",
        json={"enabled": False},
    ).json()
    assert disabled["enabled"] is False and disabled["status"] == "disabled"
    assert client.post(f"/api/projects/{project['id']}/workstreams/{stream['id']}/sync").status_code == 409

    enabled = client.patch(
        f"/api/projects/{project['id']}/workstreams/{stream['id']}",
        json={"enabled": True},
    ).json()
    assert enabled["enabled"] is True and enabled["status"] == "idle"
    assert client.post(f"/api/projects/{project['id']}/workstreams/{stream['id']}/sync").status_code == 200


def test_cancel_issue_run_cancels_pending_run_task(app, monkeypatch):
    client, store = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    _pass_preflight(monkeypatch)
    monkeypatch.setattr(
        "hive.api.fetch_open_issues_full",
        lambda repo, token: [issue(1, "first"), issue(2, "second")],
    )

    detail = client.get(f"/api/projects/{project['id']}").json()
    stream = next(w for w in detail["workstreams"] if w["kind"] == "github_issues")
    run = client.post(
        f"/api/projects/{project['id']}/workstreams/{stream['id']}/issue-runs",
        json={"scope": "selected", "issue_numbers": [1, 2]},
    ).json()["run"]

    task = store.list(Task, project_id=project["id"])[0]
    assert task.status == TaskStatus.pending and task.run_id == run["id"]

    cancelled = client.post(f"/api/issue-runs/{run['id']}/cancel").json()

    assert cancelled["status"] == IssueRunStatus.cancelled
    assert cancelled["counts"]["cancelled_tasks"] == 1
    assert store.get(Task, task.id).status == TaskStatus.cancelled
    assert store.get(Workstream, task.workstream_id).status == WorkstreamStatus.queued

    project_model = store.get(Project, project["id"])
    cancelled_run = store.get(IssueRun, run["id"])
    assert advance_issues(store, project_model, run=cancelled_run) == 0
    assert len(store.list(Task, project_id=project["id"])) == 1


def test_cancel_issue_run_signals_delivered_running_task(app):
    client, store = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    stream = next(w for w in client.get(f"/api/projects/{project['id']}").json()["workstreams"] if w["kind"] == "github_issues")
    run = store.put(
        IssueRun(
            project_id=project["id"],
            workstream_id=stream["id"],
            repo="https://github.com/o/r.git",
            status=IssueRunStatus.running,
        )
    )
    task = store.put(
        Task(
            project_id=project["id"],
            workstream_id=stream["id"],
            work_item_id=stream["id"],
            run_id=run.id,
            repo="https://github.com/o/r.git",
            instructions="review",
            status=TaskStatus.running,
            kind=TaskKind.review,
            runner_id="runner-1",
            delivered=True,
        )
    )

    cancelled = client.post(f"/api/issue-runs/{run.id}/cancel").json()

    task = store.get(Task, task.id)
    assert task.status == TaskStatus.running
    assert task.cancel_requested is True
    assert cancelled["status"] == IssueRunStatus.cancelled
    assert cancelled["counts"]["cancelled_tasks"] == 1


def test_cancel_issue_run_hard_cancels_undelivered_running_task(app):
    client, store = app
    project = client.post("/api/projects", json={"name": "spec"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": "https://github.com/o/r.git"})
    stream = next(w for w in client.get(f"/api/projects/{project['id']}").json()["workstreams"] if w["kind"] == "github_issues")
    run = store.put(
        IssueRun(
            project_id=project["id"],
            workstream_id=stream["id"],
            repo="https://github.com/o/r.git",
            status=IssueRunStatus.running,
        )
    )
    task = store.put(
        Task(
            project_id=project["id"],
            workstream_id=stream["id"],
            work_item_id=stream["id"],
            run_id=run.id,
            repo="https://github.com/o/r.git",
            instructions="resolve",
            status=TaskStatus.running,
            kind=TaskKind.resolve,
            runner_id="runner-1",
            delivered=False,
        )
    )

    cancelled = client.post(f"/api/issue-runs/{run.id}/cancel").json()

    task = store.get(Task, task.id)
    assert task.status == TaskStatus.cancelled
    assert task.cancel_requested is False
    assert "before delivery" in task.result_text
    assert cancelled["status"] == IssueRunStatus.cancelled
    assert cancelled["counts"]["cancelled_tasks"] == 1


# -- preflight ---------------------------------------------------------------


def _usable_codex(store, project):
    from hive.models import Resource, ResourceUsability, Runner

    runner = store.put(Runner(workspace_id=project.workspace_id, name="cx", backends=["codex"]))
    store.put(Resource(workspace_id=project.workspace_id, runner_id=runner.id, backend="codex",
                       usability_status=ResourceUsability.usable))


def test_preflight_checks(monkeypatch):
    from hive.workstreams._preflight import preflight_checks

    cfg = Config(gcp_project="", gcs_bucket="", gh_token="t", gemini_api_key="",
                 orch_model="", runner_token="x", data_dir=".")
    store = MemoryStore()

    project = store.put(Project(name="s", spec_repo="https://github.com/o/r.git"))
    _usable_codex(store, project)
    monkeypatch.setattr("hive.workstreams._preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": True, "has_issues": True, "default_branch": "main"})
    checks = {c.name: c for c in preflight_checks(store, cfg, project)}
    assert all(c.ok for c in checks.values() if c.hard)
    assert checks["repo_write_access"].ok and checks["codex_runner_usable"].ok

    monkeypatch.setattr("hive.workstreams._preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": False, "has_issues": True, "default_branch": "main"})
    checks = {c.name: c for c in preflight_checks(store, cfg, project)}
    assert not checks["repo_write_access"].ok  # read-only token → hard fail


def test_preflight_endpoint_queues_runner_check(app, monkeypatch):
    client, store = app
    pid = _issues_project_via_api(client)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    monkeypatch.setattr("hive.workstreams._preflight.repo_permissions",
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
    monkeypatch.setattr("hive.workstreams._preflight.repo_permissions",
                        lambda repo, token: {"full_name": "o/r", "push": False, "has_issues": True, "default_branch": "main"})
    resp = client.post(f"/api/projects/{pid}/scan-issues")
    assert resp.status_code == 409
    assert any(c["name"] == "repo_write_access" for c in resp.json()["detail"]["checks"])
