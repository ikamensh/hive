import subprocess

from fastapi.testclient import TestClient

from hive.blobstore import LocalBlobStore
from hive.config import Config
from hive.models import (
    Finding,
    HumanTask,
    Project,
    Story,
    StoryStatus,
    Task,
    TaskKind,
    TaskStatus,
    TestEpisode as EpisodeModel,
)
from hive.store import MemoryStore
from hive.supervisor import Supervisor
from hive.testing import ensure_testing_workstream, reconcile_stories
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


def _report(client, task_id, text, is_error=False):
    client.post(
        f"/api/tasks/{task_id}/result",
        json={"text": text, "is_error": is_error},
        headers=RUNNER_HEADERS,
    )


def test_reconcile_stories_from_acceptance(tmp_path):
    store = MemoryStore()
    repo = spec_repo(tmp_path)
    project = store.put(Project(name="p", spec_repo=str(repo)))
    stream = ensure_testing_workstream(store, project)

    notes, baseline = reconcile_stories(store, project, stream, repo)

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
