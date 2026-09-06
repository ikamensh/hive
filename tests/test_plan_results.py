"""Plan execution exports remain useful without an internal planner."""

import httpx
import pytest

from test_plans import app as app

from hive._workstreams import plans
from hive.models import HumanTask, Plan, PlanItem, PlanItemStatus, PlanStatus, Project, Question, Task, TaskStatus
from hive.persistence.store import MemoryStore


def test_export_preserves_task_order_and_distinguishes_cancelled_work():
    """A partially delivered plan reports its actual outcomes, without claiming
    that the higher project goal was achieved or requiring an LLM summary."""
    from hive._workstreams.plan_results import result_document

    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git", included_only=True))
    plan = plans.create_draft(store, project, "Ship search", [{"title": "Search"}, {"title": "Filters"}])
    first, second = plans.plan_items(store, plan)
    plans.cancel_item(store, second, "Owner deferred filtering")
    store.update(PlanItem, first.id, lambda item: setattr(item, "status", PlanItemStatus.done))
    store.update(Plan, plan.id, lambda current: setattr(current, "status", PlanStatus.approved))
    plan = plans.refresh_plan(store, store.get(Plan, plan.id))

    result = result_document(store, project, plan)

    assert result["schema_version"] == 1
    assert result["plan_id"] == plan.id and result["project_id"] == project.id
    assert result["terminal"] and result["status"] == "complete"
    assert result["outcome"] == "partial"
    assert result["counts"] == {"total": 2, "done": 1, "cancelled": 1, "remaining": 0}
    assert [(item["title"], item["status"]) for item in result["items"]] == [("Search", "done"), ("Filters", "cancelled")]
    assert result["items"][1]["parked_reason"] == "Owner deferred filtering"
    assert not store.get(Project, project.id).goal_complete


def test_result_includes_landing_and_runner_evidence(tmp_path):
    """Build and review reports survive landing as a machine-readable receipt,
    including the actual merge SHA and the exact pushed SHA that passed checks."""
    from hive._workstreams.plan_results import result_document
    from hive.runner._task_results import TaskResult
    from test_plans import make_processor, only_resolve_task, report

    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git", validation_command="make check"))
    plan = plans.create_draft(store, project, "Ship search", [{"title": "Search"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    processor, _ = make_processor(store, tmp_path, merge=lambda *args, **kwargs: "a" * 40)
    builder = only_resolve_task(store, project)
    report(store, processor, builder, "Search implemented.\nOUTCOME: FIXED")
    reviewer = store.list(Task, project_id=project.id, kind="review")[0]
    def uploaded_review(task):
        task.status = TaskStatus.running
        task.trace_blob = "review.jsonl"
        task.artifact_blobs = ["evidence.txt"]

    store.update(Task, reviewer.id, uploaded_review)
    processor.handle(reviewer.id, TaskResult(
        text="Boundary case and search pass.\nREVIEW: ACCEPT",
        validation={"command": "make check", "exit_code": 0, "output": "24 passed", "commit_sha": "b" * 40},
    ), project.workspace_id)

    result = result_document(store, project, store.get(Plan, plan.id))

    assert result["outcome"] == "done" and result["terminal"]
    item = result["items"][0]
    assert item["merged_commit_sha"] == "a" * 40 and item["merged_at"] > 0
    build, review = item["attempts"]
    assert build["task_id"] == builder.id and "Search implemented" in build["result_text"]
    assert review["task_id"] == reviewer.id and review["verdict"] == "accept"
    assert review["validation"]["commit_sha"] == "b" * 40
    assert review["validation"]["output"] == "24 passed"
    assert review["trace_blob"] == "review.jsonl" and review["artifact_blobs"] == ["evidence.txt"]


@pytest.mark.parametrize("status_code", [201, 204])
def test_github_landing_returns_the_resulting_commit(monkeypatch, status_code):
    """Both a new merge and an already-landed retry return concrete commit
    evidence; a successful HTTP status alone is not a landing receipt."""
    from hive._workstreams.issues import merge_branch

    sha = "c" * 40

    def get(url, **kwargs):
        payload = {"sha": sha} if "/commits/" in url else {"default_branch": "main"}
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    def post(url, **kwargs):
        return httpx.Response(status_code, json={"sha": sha} if status_code == 201 else None,
                              request=httpx.Request("POST", url))

    monkeypatch.setattr("hive._workstreams.issues.httpx.get", get)
    monkeypatch.setattr("hive._workstreams.issues.httpx.post", post)

    assert merge_branch("https://github.com/o/r.git", "hive/plan-test", "token") == sha


def test_abandonment_is_not_terminal_while_an_attempt_is_draining():
    """Stopping a plan must not tell a supervising agent execution has ended
    while a previously dispatched process is still running."""
    from hive._workstreams.plan_results import result_document

    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    plan = plans.create_draft(store, project, "Ship search", [{"title": "Search"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    task = store.list(Task, project_id=project.id)[0]
    store.update(Task, task.id, lambda task: setattr(task, "status", TaskStatus.running))
    plan = plans.abandon_plan(store, store.get(Plan, plan.id))

    draining = result_document(store, project, plan)
    assert not draining["terminal"] and draining["outcome"] == "stopping"
    assert draining["finished_at"] == 0

    store.update(Task, task.id, lambda task: setattr(task, "status", TaskStatus.cancelled))
    stopped = result_document(store, project, plan)
    assert stopped["terminal"] and stopped["outcome"] == "abandoned"


def test_result_carries_relevant_attention_and_submitted_revision():
    """A supervising agent receives open decisions and repairs for this plan,
    without unrelated project/workstream records or a lost submission identity."""
    from hive._workstreams.plan_results import result_document

    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    plan = plans.create_draft(store, project, "Ship search", [{"title": "Search"}])
    item = plans.plan_items(store, plan)[0]
    question = store.put(Question(project_id=project.id, workstream_id=item.id, text="Which records can be searched?"))
    todo = store.put(HumanTask(project_id=project.id, title="Fix landing access", instructions="Grant push access",
                               resolution={"check": "plan_item_done", "plan_item_id": item.id}))
    store.put(Question(project_id="other", text="Another project"))
    store.put(Question(project_id=project.id, workstream_id="other-issue", text="Another workstream"))
    store.put(HumanTask(project_id=project.id, title="Another plan", instructions="Ignore",
                        resolution={"check": "plan_item_done", "plan_item_id": "other-item"}))

    result = result_document(store, project, plan)

    assert result["revision"] == plan.revision and result["input_digest"] == plan.input_digest
    assert [q["id"] for q in result["questions"]] == [question.id]
    assert [t["id"] for t in result["todos"]] == [todo.id]


def test_plannerless_completion_has_a_truthful_state_reason():
    """An imported plan can finish with no planner configured; the project
    should direct its operator to the result without inventing goal success."""
    from hive._control.supervisor import Supervisor, state_reason

    store = MemoryStore()
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git", included_only=True))
    plan = store.put(Plan(project_id=project.id, goal="Ship search", status="complete"))
    store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="Search", status="done"))
    store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="Filters", status="cancelled"))
    supervisor = Supervisor(store, lambda *_: pytest.fail("No planner is configured"))
    supervisor.refresh_state(project)

    reason = state_reason(store, project, set(), 0)

    assert "plan complete" in reason and "1 merged" in reason and "1 cancelled" in reason
    assert "result" in reason and "ask Hive to propose" not in reason
    assert not store.get(Project, project.id).goal_complete


@pytest.fixture
def running_plan(app, monkeypatch):
    from test_api_e2e import _register_usable_runner
    from test_plans import FakeSpecRepo

    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    monkeypatch.setattr("hive.api.merge_branch", lambda *args, **kwargs: "d" * 40)
    monkeypatch.setattr("hive.api.delete_branch", lambda *args, **kwargs: None)
    project = client.post("/api/projects", json={"name": "demo"}).raise_for_status().json()
    pid = project["id"]
    client.patch(f"/api/projects/{pid}", json={
        "spec_repo": "https://github.com/o/r.git", "included_only": True,
        "testing_auto": False, "build_backend": "codex", "review_backend": "codex",
    }).raise_for_status()
    plan = client.post(f"/api/projects/{pid}/plan", json={
        "goal": "Ship search", "items": [{"title": "Search", "notes": "Verify empty input"}],
    }).raise_for_status().json()["plan"]
    client.post(f"/api/plans/{plan['id']}/approve").raise_for_status()
    rid = _register_usable_runner(client, backend="codex")
    return client, store, plan, rid


def test_http_result_reports_a_complete_imported_run_without_planning(running_plan):
    """An external operator can submit, execute, and retrieve a versioned
    result through the real HTTP protocol with no intake or planner call."""
    from test_api_e2e import _pump
    from test_cli import RUNNER_HEADERS

    client, store, plan, rid = running_plan

    for report in ("Search implemented.\nOUTCOME: FIXED", "Empty input verified.\nREVIEW: ACCEPT"):
        _pump(client, store)
        task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).raise_for_status().json()["task"]
        client.post(f"/api/tasks/{task['id']}/result", headers=RUNNER_HEADERS,
                    json={"text": report}).raise_for_status()

    result = client.get(f"/api/plans/{plan['id']}/result").raise_for_status().json()
    assert result["terminal"] and result["outcome"] == "done"
    assert result["input_digest"] and result["revision"] >= 1
    item = result["items"][0]
    assert item["merged_commit_sha"] == "d" * 40
    assert all(attempt["plan_revision"] >= 1 for attempt in item["attempts"])
    assert client.get(f"/api/projects/{plan['project_id']}").json()["project"]["goal_complete"] is False


def test_runner_cancellation_preserves_abandoned_item_outcome(running_plan):
    """A late cancellation report ends draining without reopening an abandoned
    item as blocked or removing it from the receipt's cancelled count."""
    from test_api_e2e import _pump
    from test_cli import RUNNER_HEADERS

    client, store, plan, rid = running_plan
    _pump(client, store)
    task = client.post(f"/api/runners/{rid}/poll", headers=RUNNER_HEADERS).raise_for_status().json()["task"]
    client.post(f"/api/plans/{plan['id']}/abandon").raise_for_status()
    assert not client.get(f"/api/plans/{plan['id']}/result").json()["terminal"]

    client.post(f"/api/tasks/{task['id']}/result", headers=RUNNER_HEADERS,
                json={"text": "Stopped as requested", "cancelled": True}).raise_for_status()
    result = client.get(f"/api/plans/{plan['id']}/result").raise_for_status().json()

    assert result["terminal"] and result["outcome"] == "abandoned"
    assert result["counts"] == {"total": 1, "done": 0, "cancelled": 1, "remaining": 0}
    assert result["items"][0]["status"] == "cancelled"
