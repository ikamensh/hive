import subprocess

from fastapi.testclient import TestClient

from hive.persistence.blobstore import LocalBlobStore
from hive.config.settings import Config
from hive.models import (
    Finding,
    FindingStatus,
    HumanTask,
    Project,
    Question,
    Story,
    StoryFidelity,
    StoryOracleStatus,
    StoryStatus,
    Task,
    TaskKind,
    TaskStatus,
    TestEpisode as EpisodeModel,
)
from hive.persistence.store import MemoryStore
from hive.control.supervisor import Supervisor
from hive.workstreams._testing import ensure_testing_workstream, file_or_update_finding_issue, reconcile_story_backlog
from tests.test_api_e2e import RUNNER_HEADERS, _pump, _register_usable_runner


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def spec_repo(tmp_path):
    repo = tmp_path / "spec-source"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.invalid"], repo)
    _git(["config", "user.name", "Hive Test"], repo)
    (repo / "mission.md").write_text("# Mission\n\nMake Beacon reliable.\n")
    (repo / "iteration.md").write_text("# Iteration\n\nHarden login and webhooks.\n")
    (repo / "acceptance").mkdir()
    (repo / "acceptance" / "webhook-retry.md").write_text(
        "# story: webhook-retry [api]\n"
        "As an integrator I get reliable webhook retries.\n\n"
        "## Rules\n"
        "- Failed webhooks retry with backoff.\n\n"
        "## Examples\n"
        "- Given an endpoint returns 500\n"
        "  When retries run\n"
        "  Then more than one retry is attempted\n"
    )
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "spec"], repo)
    return repo


def empty_spec_repo(tmp_path):
    repo = tmp_path / "spec-source"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.invalid"], repo)
    _git(["config", "user.name", "Hive Test"], repo)
    (repo / "mission.md").write_text("# Mission\n\nMake Beacon reliable.\n")
    (repo / "iteration.md").write_text("# Iteration\n\nHarden login and webhooks.\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "spec"], repo)
    return repo


def add_login_story(repo):
    (repo / "acceptance").mkdir(exist_ok=True)
    (repo / "acceptance" / "login.md").write_text(
        "# story: login [api]\n"
        "As a user I can sign in so that I can access my dashboard.\n\n"
        "## Rules\n"
        "- Valid users reach the dashboard.\n\n"
        "## Examples\n"
        "- Given valid credentials\n"
        "  When I sign in\n"
        "  Then I see the dashboard\n"
    )
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "add login story"], repo)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def app(tmp_path, repo):
    store = MemoryStore()
    supervisor = Supervisor(store, lambda pid, events: None)
    config = Config(
        gcp_project="",
        gcs_bucket="",
        gh_token="token",
        gemini_api_key="",
        orch_model="",
        runner_token="test-token",
        data_dir=tmp_path / "data",
    )
    from hive.api import create_app

    return TestClient(create_app(store, supervisor, config, blobs=LocalBlobStore(tmp_path / "blobs"))), store


def _project_with_repo(client, repo):
    project = client.post("/api/projects", json={"name": "beacon"}).json()
    client.patch(f"/api/projects/{project['id']}", json={"spec_repo": str(repo)})
    return project["id"]


def _poll(client, rid):
    return client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).json()["task"]


def _report(client, task_id, text, is_error=False, structured_result=None):
    body = {"text": text, "is_error": is_error}
    if structured_result is not None:
        body["structured_result"] = structured_result
    client.post(f"/api/tasks/{task_id}/result", json=body, headers=RUNNER_HEADERS)


def test_reconcile_stories_from_acceptance(tmp_path):
    store = MemoryStore()
    repo = spec_repo(tmp_path)
    project = store.put(Project(name="p", spec_repo=str(repo)))
    stream = ensure_testing_workstream(store, project)

    report = reconcile_story_backlog(store, project, stream, repo)
    notes, baseline = report.notes, report.baseline

    stories = store.list(Story, project_id=project.id)
    assert notes == ["added story webhook-retry"]
    assert baseline
    assert len(stories) == 1
    assert stories[0].key == "webhook-retry"
    assert stories[0].status == StoryStatus.untested
    assert "Failed webhooks retry" in stories[0].acceptance


def test_testing_episode_files_confirmed_bug_issue(tmp_path, monkeypatch):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    detail = client.get(f"/api/projects/{pid}").json()
    stream = next(w for w in detail["workstreams"] if w["kind"] == "testing")

    created = client.post(
        f"/api/projects/{pid}/workstreams/{stream['id']}/test-episodes",
        json={"scope": "full"},
    ).json()
    episode_id = created["episode"]["id"]

    _pump(client, store)
    refresh = _poll(client, rid)
    assert refresh["kind"] == "test_refresh"
    _report(client, refresh["id"], "Refreshed acceptance.\nREFRESH: DONE")

    stories = store.list(Story, project_id=pid)
    assert [s.key for s in stories] == ["webhook-retry"]
    _pump(client, store)
    sweep = _poll(client, rid)
    assert sweep["kind"] == "test_sweep"
    _report(
        client,
        sweep["id"],
        "Webhook retry stopped after one attempt.\n"
        "SWEEP: FINDINGS\n"
        "```json\n"
        '{"fidelity":"local","findings":[{"kind":"bug","severity":"high",'
        '"summary":"Webhook retries stop after one attempt","detail":"Return 500 and observe one retry.",'
        '"oracle":"Failed webhooks must retry with backoff","evidence_blobs":["retry.log"]}]}\n'
        "```",
    )

    finding = store.list(Finding, project_id=pid)[0]
    assert finding.status == "suspected"
    _pump(client, store)
    repro = _poll(client, rid)
    assert repro["kind"] == "test_reproduce"

    filed = {}

    def fake_file(repo_ref, finding, story, token):
        filed["repo"] = repo_ref
        filed["summary"] = finding.summary
        filed["story"] = story.key
        return 99, "https://github.com/acme/beacon/issues/99"

    monkeypatch.setattr("hive.api.file_or_update_finding_issue", fake_file)
    _report(client, repro["id"], "Reproduced from scratch.\nREPRO: CONFIRMED")

    finding = store.get(Finding, finding.id)
    story = store.get(Story, stories[0].id)
    episode = store.get(EpisodeModel, episode_id)
    assert filed["story"] == "webhook-retry"
    assert finding.status == "confirmed"
    assert finding.issue_number == 99
    assert story.status == "failing"
    assert story.open_issue_number == 99
    assert episode.status == "done"


def test_testing_episode_accepts_structured_results_without_markers(tmp_path, monkeypatch):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    detail = client.get(f"/api/projects/{pid}").json()
    stream = next(w for w in detail["workstreams"] if w["kind"] == "testing")

    created = client.post(
        f"/api/projects/{pid}/workstreams/{stream['id']}/test-episodes",
        json={"scope": "full"},
    ).json()
    episode_id = created["episode"]["id"]

    _pump(client, store)
    refresh = _poll(client, rid)
    _report(
        client,
        refresh["id"],
        "Refreshed acceptance without the legacy marker.",
        structured_result={
            "task_id": refresh["id"],
            "outcome": "done",
            "active_story_count": 1,
            "stories_changed": ["webhook-retry"],
            "created_story_keys": [],
            "updated_story_keys": [],
            "archived_story_keys": [],
            "changed_files": [],
            "commit_sha": "",
            "questions": [],
        },
    )

    story = store.list(Story, project_id=pid)[0]
    _pump(client, store)
    sweep = _poll(client, rid)
    _report(
        client,
        sweep["id"],
        "Found a retry bug without the legacy marker.",
        structured_result={
            "task_id": sweep["id"],
            "outcome": "findings",
            "fidelity": "docker",
            "findings": [
                {
                    "kind": "bug",
                    "severity": "high",
                    "summary": "Webhook retries stop after one attempt",
                    "detail": "Return 500 and observe one retry.",
                    "oracle": "Failed webhooks must retry with backoff",
                    "evidence_blobs": ["retry.log"],
                }
            ],
        },
    )

    finding = store.list(Finding, project_id=pid)[0]
    story = store.get(Story, story.id)
    assert finding.status == "suspected"
    assert story.status == "failing"
    assert story.last_fidelity == StoryFidelity.docker

    _pump(client, store)
    repro = _poll(client, rid)
    assert repro["kind"] == "test_reproduce"

    filed = {}

    def fake_file(repo_ref, finding, story, token):
        filed["story"] = story.key
        return 101, "https://github.com/acme/beacon/issues/101"

    monkeypatch.setattr("hive.api.file_or_update_finding_issue", fake_file)
    _report(
        client,
        repro["id"],
        "Confirmed without the legacy marker.",
        structured_result={
            "task_id": repro["id"],
            "outcome": "confirmed",
            "evidence_blobs": ["retry.log"],
        },
    )

    finding = store.get(Finding, finding.id)
    story = store.get(Story, story.id)
    episode = store.get(EpisodeModel, episode_id)
    assert filed["story"] == "webhook-retry"
    assert finding.status == "confirmed"
    assert story.open_issue_number == 101
    assert episode.status == "done"


def test_testing_episode_blocks_when_refresh_leaves_no_stories(tmp_path):
    repo = empty_spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    created = client.post(
        f"/api/projects/{pid}/workstreams/{stream['id']}/test-episodes",
        json={"scope": "full"},
    ).json()
    episode_id = created["episode"]["id"]

    _pump(client, store)
    refresh = _poll(client, rid)
    _report(client, refresh["id"], "Refreshed acceptance.\nREFRESH: DONE")

    episode = store.get(EpisodeModel, episode_id)
    assert episode.status == "failed"
    assert episode.counts["failure"] == "test refresh produced no active acceptance stories"
    assert store.list(Story, project_id=pid) == []
    assert store.list(Task, project_id=pid, kind=TaskKind.test_sweep) == []
    todos = store.list(HumanTask, project_id=pid)
    assert [todo.title for todo in todos] == ["Repair testing refresh for beacon"]
    assert "not safe to sweep" in todos[0].instructions


def test_testing_episode_marks_refresh_created_stories_as_draft(tmp_path):
    repo = empty_spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    created = client.post(
        f"/api/projects/{pid}/workstreams/{stream['id']}/test-episodes",
        json={"scope": "full"},
    ).json()
    episode_id = created["episode"]["id"]

    _pump(client, store)
    refresh = _poll(client, rid)
    sha = add_login_story(repo)
    _report(
        client,
        refresh["id"],
        "Created the first acceptance story.",
        structured_result={
            "task_id": refresh["id"],
            "outcome": "done",
            "active_story_count": 1,
            "stories_changed": ["login"],
            "created_story_keys": ["login"],
            "updated_story_keys": [],
            "archived_story_keys": [],
            "changed_files": ["acceptance/login.md"],
            "commit_sha": sha,
            "questions": [],
        },
    )

    story = store.list(Story, project_id=pid)[0]
    episode = store.get(EpisodeModel, episode_id)
    assert story.key == "login"
    assert story.oracle_status == StoryOracleStatus.draft
    assert "created by Hive" in story.oracle_status_reason
    assert episode.status == "sweeping"
    assert episode.counts["draft_stories"] == 1
    _pump(client, store)
    sweep = _poll(client, rid)
    assert sweep["kind"] == "test_sweep"
    assert "Story key: login" in sweep["instructions"]


def test_testing_refresh_files_structured_questions(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    client.post(
        f"/api/projects/{pid}/workstreams/{stream['id']}/test-episodes",
        json={"scope": "full"},
    ).json()

    _pump(client, store)
    refresh = _poll(client, rid)
    _report(
        client,
        refresh["id"],
        "Refresh has a material question.",
        structured_result={
            "task_id": refresh["id"],
            "outcome": "done",
            "active_story_count": 1,
            "stories_changed": [],
            "created_story_keys": [],
            "updated_story_keys": [],
            "archived_story_keys": [],
            "changed_files": [],
            "commit_sha": "",
            "questions": ["Should webhook retries include 4xx responses or only 5xx responses?"],
        },
    )

    questions = store.list(Question, project_id=pid, workstream_id=stream["id"])
    assert len(questions) == 1
    assert "testing refresh" in questions[0].text
    assert "4xx responses" in questions[0].text


def test_artifact_upload_roundtrip(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    task = store.put(
        Task(
            project_id=pid,
            workstream_id="story",
            repo=str(repo),
            instructions="test",
            status=TaskStatus.running,
            kind=TaskKind.test_sweep,
        )
    )

    posted = client.post(
        f"/api/tasks/{task.id}/artifacts/screens/one.txt",
        content=b"artifact",
        headers=RUNNER_HEADERS,
    )
    assert posted.status_code == 200
    assert store.get(Task, task.id).artifact_blobs == ["screens/one.txt"]

    got = client.get(f"/api/tasks/{task.id}/artifacts/screens/one.txt")
    assert got.status_code == 200
    assert got.content == b"artifact"
    assert client.post(
        f"/api/tasks/{task.id}/artifacts/../bad.txt",
        content=b"x",
        headers=RUNNER_HEADERS,
    ).status_code in {400, 404}


def test_file_testing_issue_creates_custom_labels(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

        def json(self):
            return self._payload

    def fake_post(url, json, headers, timeout):
        calls.append((url, json))
        if url.endswith("/labels"):
            return Response(201)
        if url.endswith("/issues"):
            return Response(201, {"number": 12, "html_url": "https://github.com/acme/hive/issues/12"})
        raise AssertionError(url)

    monkeypatch.setattr("hive.workstreams._testing.httpx.post", fake_post)
    story = Story(
        project_id="p",
        workstream_id="w",
        repo="https://github.com/acme/hive",
        key="login",
        oracle_status=StoryOracleStatus.draft,
        oracle_status_reason="created by Hive's testing refresh from spec intention",
    )
    finding = Finding(
        project_id="p",
        workstream_id="w",
        repo=story.repo,
        episode_id="e",
        story_key=story.key,
        summary="Login fails",
        expected="The user lands on the dashboard after submitting valid credentials.",
        actual="The user is bounced back to the login form with no error message.",
        detail="1. Open /login 2. Submit valid credentials 3. Observe redirect to /login",
    )

    number, url = file_or_update_finding_issue(story.repo, finding, story, "token")

    assert number == 12
    assert url.endswith("/12")
    assert [body["name"] for request_url, body in calls if request_url.endswith("/labels")] == ["hive-test"]
    issue_body = next(body for request_url, body in calls if request_url.endswith("/issues"))
    assert issue_body["labels"] == ["hive-test", "bug"]
    body = issue_body["body"]
    assert "## What should happen" in body
    assert "## What happened instead" in body
    assert "## Steps to reproduce" in body
    assert finding.expected in body and finding.actual in body
    assert "## Oracle status" in body
    assert "created by Hive's testing refresh" in body


def _start_episode_to_sweep(client, store, pid, rid, stream_id):
    created = client.post(
        f"/api/projects/{pid}/workstreams/{stream_id}/test-episodes",
        json={"scope": "full"},
    ).json()
    episode_id = created["episode"]["id"]
    _pump(client, store)
    refresh = _poll(client, rid)
    _report(client, refresh["id"], "Refreshed acceptance.\nREFRESH: DONE")
    _pump(client, store)
    sweep = _poll(client, rid)
    assert sweep["kind"] == "test_sweep"
    return episode_id, sweep


def test_malformed_sweep_findings_block_story_and_file_todo(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    _episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    _report(client, sweep["id"], "Found a problem.\nSWEEP: FINDINGS\nnot json")

    story = store.list(Story, project_id=pid)[0]
    assert story.status == StoryStatus.blocked
    assert store.list(Finding, project_id=pid) == []
    todos = store.list(HumanTask, project_id=pid)
    assert [t.title for t in todos] == ["Repair testing sweep output for webhook-retry"]
    assert "missing or malformed findings JSON" in todos[0].instructions


def test_blocked_sweep_files_actionable_todo(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    _episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    task = store.get(Task, sweep["id"])
    task.artifact_blobs = ["setup.log"]
    store.put(task)
    _report(
        client,
        sweep["id"],
        "Could not start the app.\nSWEEP: BLOCKED",
        structured_result={
            "task_id": sweep["id"],
            "outcome": "blocked",
            "summary": "local app failed to start",
            "fidelity": "local",
            "findings": [],
        },
    )

    story = store.list(Story, project_id=pid)[0]
    assert story.status == StoryStatus.blocked
    todos = store.list(HumanTask, project_id=pid)
    assert [t.title for t in todos] == ["Unblock testing sweep for webhook-retry"]
    assert "local app failed to start" in todos[0].instructions
    assert "`setup.log`" in todos[0].instructions


def test_confirmation_error_blocks_finding_instead_of_rejecting(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    _episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    _report(
        client,
        sweep["id"],
        "Found a retry bug.\n"
        "SWEEP: FINDINGS\n"
        "```json\n"
        '{"fidelity":"local","findings":[{"kind":"bug","severity":"high",'
        '"summary":"Webhook retries stop after one attempt",'
        '"detail":"Return 500 from the webhook endpoint and observe that Hive records only one retry attempt.",'
        '"oracle":"Failed webhooks must retry with backoff"}]}\n'
        "```",
    )
    finding = store.list(Finding, project_id=pid)[0]
    _pump(client, store)
    repro = _poll(client, rid)

    _report(client, repro["id"], "checkout failed: duplicate Authorization header", is_error=True)

    finding = store.get(Finding, finding.id)
    story = store.list(Story, project_id=pid)[0]
    assert finding.status == FindingStatus.blocked
    assert story.status == StoryStatus.blocked
    todos = store.list(HumanTask, project_id=pid)
    assert [t.title for t in todos] == ["Unblock testing confirmation for webhook-retry"]
    assert "blocked, not rejected" in todos[0].instructions


def test_sweep_finding_prefers_explicit_evidence_over_all_task_artifacts(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    _episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    task = store.get(Task, sweep["id"])
    task.artifact_blobs = ["specific.log", "node_modules/tool/index.js", "everything.log"]
    store.put(task)
    _report(
        client,
        sweep["id"],
        "Found a retry bug.\n"
        "SWEEP: FINDINGS\n"
        "```json\n"
        '{"fidelity":"local","findings":[{"kind":"bug","severity":"high",'
        '"summary":"Webhook retries stop after one attempt",'
        '"detail":"Return 500 from the webhook endpoint and observe that Hive records only one retry attempt.",'
        '"oracle":"Failed webhooks must retry with backoff",'
        '"evidence_blobs":["specific.log"]}]}\n'
        "```",
    )

    finding = store.list(Finding, project_id=pid)[0]
    assert finding.evidence_blobs == ["specific.log"]


def test_sweep_finding_without_evidence_falls_back_to_screenshots_only(tmp_path):
    # Regression for issue #8: when the agent omits evidence_blobs, the issue body
    # dumped the entire artifact scratch tree (an installed playwright toolchain),
    # producing a ~180-line Evidence list. The fallback must keep screenshots only.
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    _episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    task = store.get(Task, sweep["id"])
    task.artifact_blobs = [
        "before.png",
        "after.png",
        "project.json",
        "root.html",
        "tools/node_modules/playwright/index.js",
    ]
    store.put(task)
    _report(
        client,
        sweep["id"],
        "Found a retry bug.\n"
        "SWEEP: FINDINGS\n"
        "```json\n"
        '{"fidelity":"local","findings":[{"kind":"bug","severity":"high",'
        '"summary":"Webhook retries stop after one attempt",'
        '"detail":"Return 500 from the webhook endpoint and observe only one retry.",'
        '"oracle":"Failed webhooks must retry with backoff"}]}\n'
        "```",
    )

    finding = store.list(Finding, project_id=pid)[0]
    assert finding.evidence_blobs == ["before.png", "after.png"]


def test_weak_sweep_findings_are_not_filed(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    _episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    _report(
        client,
        sweep["id"],
        "Button color is a bit off.\n"
        "SWEEP: FINDINGS\n"
        "```json\n"
        '{"fidelity":"local","findings":[{"kind":"ux_smell","severity":"low",'
        '"summary":"Button color looks off",'
        '"detail":"The button color is a little different from the rest of the page but the flow still works.",'
        '"oracle":"The page should feel polished."}]}\n'
        "```",
    )

    story = store.list(Story, project_id=pid)[0]
    assert story.status == StoryStatus.blocked
    assert store.list(Finding, project_id=pid) == []
    assert "cosmetic or low-impact nitpick" in store.list(HumanTask, project_id=pid)[0].instructions


def test_cancel_testing_episode_signals_running_tasks(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    rid = _register_usable_runner(client, name="codex-runner", backend="codex")
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")

    episode_id, sweep = _start_episode_to_sweep(client, store, pid, rid, stream["id"])
    cancelled = client.post(f"/api/test-episodes/{episode_id}/cancel").json()

    task = store.get(Task, sweep["id"])
    assert task.status == TaskStatus.running
    assert task.cancel_requested is True
    assert cancelled["status"] == "cancelled"
    assert cancelled["counts"]["cancelled_tasks"] == 1


def test_cancel_testing_episode_hard_cancels_undelivered_running_tasks(tmp_path):
    repo = spec_repo(tmp_path)
    client, store = app(tmp_path, repo)
    pid = _project_with_repo(client, repo)
    stream = next(w for w in client.get(f"/api/projects/{pid}").json()["workstreams"] if w["kind"] == "testing")
    episode = store.put(
        EpisodeModel(
            project_id=pid,
            workstream_id=stream["id"],
            repo=str(repo),
            story_keys=["webhook-retry"],
        )
    )
    task = store.put(
        Task(
            project_id=pid,
            workstream_id="story",
            work_item_id="story",
            run_id=episode.id,
            repo=str(repo),
            instructions="test",
            status=TaskStatus.running,
            kind=TaskKind.test_sweep,
            runner_id="runner-1",
            delivered=False,
        )
    )

    cancelled = client.post(f"/api/test-episodes/{episode.id}/cancel").json()

    task = store.get(Task, task.id)
    assert task.status == TaskStatus.cancelled
    assert task.cancel_requested is False
    assert "before delivery" in task.result_text
    assert cancelled["counts"]["cancelled_tasks"] == 1
