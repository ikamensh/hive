"""CI auto-fix: a red default-branch build becomes a GitHub issue, then the
existing issue-solving pipeline fixes it.

`fetch_ci_status` parsing is tested directly with httpx mocked; the
file→reconcile→advance orchestration is tested against MemoryStore with the
GitHub network functions stubbed (so we assert the reuse of the issue pipeline,
not GitHub). Supervisor gating is tested as a pure predicate.
"""

import asyncio

import pytest

from hive.config.settings import Config
from hive.control.supervisor import Supervisor
from hive.models import (
    Project,
    Task,
    TaskKind,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)
from hive.persistence.store import MemoryStore
from hive.workstreams.ci import (
    CiCheckResult,
    CiConclusion,
    CiStatus,
    check_and_autofix,
    ci_issue_body,
    fetch_ci_status,
)
from hive.workstreams.issues import ensure_issue_workstream


class Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def fake_get(check_runs, statuses, sha="abc1234"):
    def _get(url, **kwargs):
        if url.endswith("/check-runs"):
            return Resp({"check_runs": check_runs})
        if url.endswith("/status"):
            return Resp({"statuses": statuses})
        return Resp({"sha": sha})  # commits/{branch}

    return _get


def ci_project(store) -> Project:
    return store.put(Project(name="p", spec_repo="https://github.com/o/r.git"))


# -- status parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "check_runs,statuses,expected",
    [
        ([{"name": "build", "status": "completed", "conclusion": "failure", "html_url": "u"}], [], CiConclusion.failing),
        ([{"name": "build", "status": "completed", "conclusion": "success"}], [], CiConclusion.passing),
        ([{"name": "build", "status": "in_progress", "conclusion": None}], [], CiConclusion.pending),
        ([], [], CiConclusion.none),
        ([], [{"state": "failure", "context": "ci/x", "target_url": "u"}], CiConclusion.failing),
        ([], [{"state": "success", "context": "ci/x"}], CiConclusion.passing),
    ],
)
def test_fetch_ci_status_conclusions(monkeypatch, check_runs, statuses, expected):
    monkeypatch.setattr("hive.workstreams.ci.default_branch", lambda repo, token: "main")
    monkeypatch.setattr("hive.workstreams.ci.httpx.get", fake_get(check_runs, statuses))
    status = fetch_ci_status("o/r", "tok")
    assert status.conclusion == expected
    if expected == CiConclusion.failing:
        assert status.failing_checks  # the failing check name is captured
        assert status.sha == "abc1234"


# -- orchestration: file when red, then reuse the issue pipeline -------------


def _stub_failing(monkeypatch, *, open_issues, sha="deadbeef"):
    status = CiStatus(repo="https://github.com/o/r.git", branch="main", sha=sha,
                      conclusion=CiConclusion.failing,
                      failing_checks=[{"name": "build", "url": "u"}])
    monkeypatch.setattr("hive.workstreams.ci.fetch_ci_status", lambda repo, token: status)
    monkeypatch.setattr("hive.workstreams.ci.fetch_open_issues_full", lambda repo, token: list(open_issues))
    return status


def test_red_ci_files_issue_and_queues_a_resolve(monkeypatch):
    store = MemoryStore()
    project = ci_project(store)
    ws = ensure_issue_workstream(store, project)
    _stub_failing(monkeypatch, open_issues=[])
    monkeypatch.setattr("hive.workstreams.ci.file_ci_issue", lambda repo, status, token, details="": (99, "https://gh/99"))

    result = check_and_autofix(store, project, ws, "tok", issue_backend="codex")

    assert result.conclusion == CiConclusion.failing
    assert result.filed_issue == 99 and not result.already_filed
    assert result.resolve_queued == 1
    # The CI failure is now an ordinary issue work item driven by the same pipeline.
    items = [w for w in store.list(Workstream, project_id=project.id) if w.source == WorkstreamSource.issue]
    assert [w.issue_number for w in items] == [99]
    assert items[0].status == WorkstreamStatus.resolving
    resolve = [t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.resolve]
    assert len(resolve) == 1
    assert resolve[0].issue_number == 99 and resolve[0].branch == "hive/issue-99"


def test_red_ci_does_not_refile_same_commit(monkeypatch):
    store = MemoryStore()
    project = ci_project(store)
    ws = ensure_issue_workstream(store, project)
    status = _stub_failing(monkeypatch, open_issues=[], sha="cafef00d")
    prior = {"number": 42, "title": "[hive-ci] CI failing", "url": "https://gh/42",
             "doc": ci_issue_body(status), "attachments": []}
    monkeypatch.setattr("hive.workstreams.ci.fetch_open_issues_full", lambda repo, token: [prior])

    def boom(*a, **k):
        raise AssertionError("should not file a duplicate CI issue for the same sha")

    monkeypatch.setattr("hive.workstreams.ci.file_ci_issue", boom)

    result = check_and_autofix(store, project, ws, "tok", issue_backend="codex")

    assert result.already_filed and result.filed_issue == 42
    items = [w for w in store.list(Workstream, project_id=project.id) if w.source == WorkstreamSource.issue]
    assert [w.issue_number for w in items] == [42]  # the existing issue, ingested once


def test_ci_issue_body_embeds_webhook_logs():
    status = CiStatus(repo="o/r", branch="main", sha="abc", conclusion=CiConclusion.failing,
                      failing_checks=[{"name": "Tests", "url": "u"}])
    body = ci_issue_body(status, details="E   assert 1 == 2\nFAILED tests/test_x.py")
    assert "## Failing CI logs" in body
    assert "FAILED tests/test_x.py" in body
    assert "hive-ci sha=abc" in body  # dedup marker still present


def test_green_ci_files_nothing(monkeypatch):
    store = MemoryStore()
    project = ci_project(store)
    ws = ensure_issue_workstream(store, project)
    green = CiStatus(repo=project.spec_repo, branch="main", sha="1", conclusion=CiConclusion.passing)
    monkeypatch.setattr("hive.workstreams.ci.fetch_ci_status", lambda repo, token: green)

    def boom(*a, **k):
        raise AssertionError("must not touch GitHub issues when CI is green")

    monkeypatch.setattr("hive.workstreams.ci.fetch_open_issues_full", boom)

    result = check_and_autofix(store, project, ws, "tok")

    assert result.conclusion == CiConclusion.passing and result.filed_issue == 0
    assert not [w for w in store.list(Workstream, project_id=project.id) if w.source == WorkstreamSource.issue]


# -- supervisor gating -------------------------------------------------------


def test_ci_check_due_respects_toggle_and_interval():
    store = MemoryStore()
    calls: list[str] = []
    sup = Supervisor(store, lambda p, e: None, ci_check=calls.append)
    on = store.put(Project(name="on", spec_repo="x", ci_autofix=True))
    off = store.put(Project(name="off", spec_repo="x", ci_autofix=False))

    assert sup._ci_check_due(on)
    assert not sup._ci_check_due(off)

    sup._last_ci_check[on.id] = __import__("time").time()
    assert not sup._ci_check_due(on)  # just checked; interval not elapsed

    no_cb = Supervisor(store, lambda p, e: None)  # ci_check not wired
    assert not no_cb._ci_check_due(on)


def test_run_ci_check_invokes_callback_and_clears_busy():
    store = MemoryStore()
    seen: list[str] = []
    sup = Supervisor(store, lambda p, e: None, ci_check=seen.append)
    sup._ci_busy.add("pid")
    asyncio.run(sup._run_ci_check("pid"))
    assert seen == ["pid"] and "pid" not in sup._ci_busy


# -- API: toggle + webhook ---------------------------------------------------


def _client(tmp_path, store, *, webhook_secret=""):
    from fastapi.testclient import TestClient

    from hive.api import create_app
    from hive.persistence.blobstore import LocalBlobStore

    config = Config(gcp_project="", gcs_bucket="", gh_token="t", gemini_api_key="",
                    orch_model="", runner_token="test-token", data_dir=tmp_path,
                    github_webhook_secret=webhook_secret)
    return TestClient(create_app(store, Supervisor(store, lambda p, e: None), config,
                                 blobs=LocalBlobStore(tmp_path / "blobs")))


def test_patch_ci_autofix_persists(tmp_path):
    store = MemoryStore()
    client = _client(tmp_path, store)
    pid = client.post("/api/projects", json={"name": "ci"}).json()["id"]
    body = client.patch(f"/api/projects/{pid}", json={"ci_autofix": True}).json()
    assert body["ci_autofix"] is True
    assert store.get(Project, pid).ci_autofix is True


def _captured_autofix(calls):
    def fake(store, project, workstream, token, *, issue_backend="", issue_model="", advance=True, details=""):
        calls.append({"project": project.id, "repo": workstream.repo, "details": details})
        return CiCheckResult(repo=workstream.repo, conclusion=CiConclusion.failing,
                             filed_issue=7, resolve_queued=1)
    return fake


def test_webhook_requires_configured_secret(tmp_path):
    store = MemoryStore()
    client = _client(tmp_path, store, webhook_secret="")  # disabled
    r = client.post("/api/ci/webhook", json={"repo": "o/r"}, headers={"Authorization": "Bearer x"})
    assert r.status_code == 503


def test_webhook_rejects_bad_secret(tmp_path):
    store = MemoryStore()
    client = _client(tmp_path, store, webhook_secret="s3cret")
    r = client.post("/api/ci/webhook", json={"repo": "o/r"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_webhook_triggers_autofix_for_matching_ci_project(tmp_path, monkeypatch):
    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://github.com/o/r.git", ci_autofix=True))
    calls: list[dict] = []
    monkeypatch.setattr("hive.api.check_and_autofix", _captured_autofix(calls))
    client = _client(tmp_path, store, webhook_secret="s3cret")

    r = client.post(
        "/api/ci/webhook",
        json={"repo": "o/r", "ref": "main", "event": "ci_failure", "details": "FAILED test_x"},
        headers={"Authorization": "Bearer s3cret"},
    )

    assert r.status_code == 200 and r.json()["matched"] == 1
    assert calls == [{"project": project.id, "repo": "https://github.com/o/r.git", "details": "FAILED test_x"}]


def test_webhook_skips_project_with_ci_autofix_off(tmp_path, monkeypatch):
    store = MemoryStore()
    store.put(Project(name="p", spec_repo="https://github.com/o/r.git", ci_autofix=False))
    calls: list[dict] = []
    monkeypatch.setattr("hive.api.check_and_autofix", _captured_autofix(calls))
    client = _client(tmp_path, store, webhook_secret="s3cret")

    r = client.post("/api/ci/webhook", json={"repo": "o/r"}, headers={"Authorization": "Bearer s3cret"})

    assert r.status_code == 200 and r.json()["matched"] == 0 and calls == []
