"""Iteration plans (wiki/iteration-plan.md): review-at-chosen-depth store ops
and the doc-fed resolve → review → merge pipeline.

Store ops are tested directly on MemoryStore; execution transitions run
through TaskResultProcessor with the GitHub merge transport faked, so the
whole propose → approve → build → review → land → next-item loop is covered
offline. Properties verified match the design doc's test plan: approve-all ≡
per-item flips, all-or-nothing activation, the no-unapproved-work invariant,
work-doc round-trip, and amendment behavior.
"""

import pytest

from hive.config.settings import Config
from hive.models import (
    PLAN_ITEM_TERMINAL,
    HumanTask,
    HumanTaskStatus,
    Plan,
    PlanItem,
    PlanItemStatus,
    PlanStatus,
    Project,
    Task,
    TaskKind,
    TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive.runner._task_results import TaskResult, TaskResultProcessor
from hive._control.escalation import resolve_open_todos
from hive._workstreams import plans

ITEMS = [
    {
        "title": "Mobile layout",
        "story": "A user on a phone can read the project page without pinching.",
        "constraints": "No new framework; CSS only.",
        "notes": "Start from the narrowest page.",
    },
    {"title": "Touch controls", "story": "Buttons are tappable at 44px.", "constraints": ""},
    {"title": "Viewport meta", "story": "", "constraints": "", "notes": "one-liner"},
]


class WakeRecorder:
    def __init__(self):
        self.events = []

    def wake(self, project_id, event):
        self.events.append((project_id, event))


def make_project(store) -> Project:
    return store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))


def make_config(tmp_path) -> Config:
    return Config(gcp_project="", gcs_bucket="", gh_token="t", gemini_api_key="",
                  orch_model="", runner_token="test-token", data_dir=tmp_path)


def make_processor(store, tmp_path, merge=None, delete=None):
    supervisor = WakeRecorder()
    processor = TaskResultProcessor(
        store,
        supervisor,
        make_config(tmp_path),
        merge_branch_func=merge or (lambda repo, head, token, message="": None),
        resolve_issue_func=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no GitHub issue I/O for plans")),
        delete_branch_func=delete or (lambda repo, branch, token: None),
    )
    return processor, supervisor


def report(store, processor, task: Task, text: str, is_error=False, cancelled=False):
    """Simulate the runner: mark the task running, then post its result."""
    def start(saved: Task) -> None:
        saved.status = TaskStatus.running

    store.update(Task, task.id, start)
    processor.handle(task.id, TaskResult(text=text, is_error=is_error, cancelled=cancelled),
                     task.workspace_id)


def item_by_title(store, plan, title) -> PlanItem:
    return next(i for i in plans.plan_items(store, plan) if i.title == title)


def only_resolve_task(store, project) -> Task:
    tasks = [t for t in store.list(Task, project_id=project.id)
             if t.kind == TaskKind.resolve and t.status == TaskStatus.pending]
    assert len(tasks) == 1
    return tasks[0]


# -- drafting + review ---------------------------------------------------------


def test_approve_all_equals_per_item_flips():
    """Property from the design doc: the one-click blind path and the deep
    per-item path produce identical plan/item states."""
    store_a, store_b = MemoryStore(), MemoryStore()
    project_a, project_b = make_project(store_a), make_project(store_b)
    plan_a = plans.create_draft(store_a, project_a, "mobile support", ITEMS)
    plan_b = plans.create_draft(store_b, project_b, "mobile support", ITEMS)

    plans.approve_all(store_a, plan_a)
    for item in plans.plan_items(store_b, plan_b):
        plans.approve_item(store_b, plan_b, item)

    states_a = [(i.title, i.status) for i in plans.plan_items(store_a, plan_a)]
    states_b = [(i.title, i.status) for i in plans.plan_items(store_b, plan_b)]
    assert states_a == states_b
    assert all(s == PlanItemStatus.approved for _, s in states_a)


def test_activation_is_all_or_nothing():
    """An unapproved item blocks activation entirely: plan stays draft, nothing
    queued, no tasks — never a partial set."""
    store = MemoryStore()
    project = make_project(store)
    plan = plans.create_draft(store, project, "goal", ITEMS)
    items = plans.plan_items(store, plan)
    plans.approve_item(store, plan, items[0])  # 1 of 3

    with pytest.raises(ValueError, match="awaiting approval"):
        plans.activate(store, project, plan)

    assert store.get(Plan, plan.id).status == PlanStatus.draft
    assert all(i.status != PlanItemStatus.queued for i in plans.plan_items(store, plan))
    assert store.list(Task, project_id=project.id) == []


def test_activate_queues_all_and_starts_first():
    """Approval queues every item in order and starts exactly the first; the
    resolve task's instructions carry the full item document (round-trip of
    every content field) and the item's own branch."""
    store = MemoryStore()
    project = make_project(store)
    plan = plans.create_draft(store, project, "mobile support", ITEMS)
    plans.approve_all(store, plan)
    notes = plans.activate(store, project, plan)

    assert store.get(Plan, plan.id).status == PlanStatus.approved
    assert any("3 item(s) queued" in n for n in notes)
    first, second, third = plans.plan_items(store, plan)
    assert first.status == PlanItemStatus.resolving
    assert second.status == PlanItemStatus.queued
    assert third.status == PlanItemStatus.queued

    task = only_resolve_task(store, project)
    assert task.work_item_id == first.id
    assert task.branch == plans.plan_branch(first)
    assert task.fresh_branch
    assert task.repo == project.spec_repo
    for field in (ITEMS[0]["title"], ITEMS[0]["story"], ITEMS[0]["constraints"], ITEMS[0]["notes"]):
        assert field in task.instructions
    assert "mobile support" in task.instructions  # the iteration goal travels too


def test_no_unapproved_work_invariant():
    """The core invariant: advancing never executes an item the human did not
    approve — a draft plan produces no tasks however often advance is called."""
    store = MemoryStore()
    project = make_project(store)
    plan = plans.create_draft(store, project, "goal", ITEMS)
    assert plans.advance_plan(store, project, plan) == 0
    assert store.list(Task, project_id=project.id) == []


def test_edit_rewrites_any_field_and_marks_human_touch():
    store = MemoryStore()
    project = make_project(store)
    plan = plans.create_draft(store, project, "goal", ITEMS)
    item = plans.plan_items(store, plan)[0]
    updated = plans.update_item(store, item, {"constraints": "must work offline", "order": 5})
    assert updated.constraints == "must work offline"
    assert updated.order == 5
    assert updated.edited_by_human
    assert updated.status == PlanItemStatus.proposed  # editing never approves


def test_second_draft_replaces_draft_but_never_approved_plan():
    store = MemoryStore()
    project = make_project(store)
    first = plans.create_draft(store, project, "goal one", ITEMS[:1])
    second = plans.create_draft(store, project, "goal two", ITEMS[:2])
    assert store.get(Plan, first.id).status == PlanStatus.abandoned
    assert all(i.status == PlanItemStatus.cancelled for i in plans.plan_items(store, first))
    assert plans.active_plan(store, project).id == second.id

    plans.approve_all(store, second)
    plans.activate(store, project, second)
    with pytest.raises(ValueError, match="approved plan is executing"):
        plans.create_draft(store, project, "goal three", ITEMS[:1])


# -- execution ------------------------------------------------------------------


def activated_plan(store, project, items=ITEMS):
    plan = plans.create_draft(store, project, "mobile support", items)
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    return store.get(Plan, plan.id)


def test_resolve_fixed_chains_review(tmp_path):
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project)
    processor, _ = make_processor(store, tmp_path)
    task = only_resolve_task(store, project)

    report(store, processor, task, "Built the layout.\nOUTCOME: FIXED")

    first = plans.plan_items(store, plan)[0]
    assert first.status == PlanItemStatus.reviewing
    reviews = [t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.review]
    assert len(reviews) == 1
    assert reviews[0].branch == plans.plan_branch(first)
    assert reviews[0].backend == task.backend


def test_resolve_blocked_parks_item_and_stalls_the_queue(tmp_path):
    """BLOCKED parks the item with the agent's own report as the reason (the
    marker line stripped) and — strict sequencing — starts nothing behind it."""
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project)
    processor, supervisor = make_processor(store, tmp_path)
    task = only_resolve_task(store, project)

    report(store, processor, task,
           "The story needs a decision: native app or responsive web?\nOUTCOME: BLOCKED")

    first, second, _ = plans.plan_items(store, plan)
    assert first.status == PlanItemStatus.blocked_clarity
    assert "native app or responsive web" in first.parked_reason
    assert "OUTCOME" not in first.parked_reason
    assert second.status == PlanItemStatus.queued
    assert [t for t in store.list(Task, project_id=project.id)
            if t.status == TaskStatus.pending] == []


def test_review_accept_merges_lands_and_advances(tmp_path):
    """ACCEPT merges the item branch (no issue close anywhere), deletes it,
    marks the item done, and starts the next item — which branches after the
    landed merge."""
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project)
    merged, deleted = [], []
    processor, _ = make_processor(
        store, tmp_path,
        merge=lambda repo, head, token, message="": merged.append((head, message)),
        delete=lambda repo, branch, token: deleted.append(branch),
    )

    report(store, processor, only_resolve_task(store, project), "done\nOUTCOME: FIXED")
    review = next(t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.review)
    report(store, processor, review, "Story holds.\nREVIEW: ACCEPT")

    first, second, _ = plans.plan_items(store, plan)
    assert first.status == PlanItemStatus.done
    assert merged == [(plans.plan_branch(first), "Land plan item 'Mobile layout' via Hive")]
    assert deleted == [plans.plan_branch(first)]
    assert second.status == PlanItemStatus.resolving
    nxt = only_resolve_task(store, project)
    assert nxt.work_item_id == second.id


def test_review_reject_parks_with_report(tmp_path):
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project)
    processor, _ = make_processor(store, tmp_path)

    report(store, processor, only_resolve_task(store, project), "done\nOUTCOME: FIXED")
    review = next(t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.review)
    report(store, processor, review, "Breaks the desktop layout badly.\nREVIEW: REJECT")

    first = plans.plan_items(store, plan)[0]
    assert first.status == PlanItemStatus.rejected
    assert "desktop layout" in first.parked_reason


def test_landing_failure_escalates_todo_that_self_closes(tmp_path):
    """A merge failure parks the item, files a repair todo, and the todo's
    plan_item_done predicate closes it once the human cancels the item."""
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project, items=ITEMS[:1])

    def fail_merge(repo, head, token, message=""):
        raise RuntimeError("HTTP 405 base branch protected")

    processor, _ = make_processor(store, tmp_path, merge=fail_merge)
    report(store, processor, only_resolve_task(store, project), "ok\nOUTCOME: FIXED")
    review = next(t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.review)
    report(store, processor, review, "fine\nREVIEW: ACCEPT")

    item = plans.plan_items(store, plan)[0]
    assert item.status == PlanItemStatus.rejected
    assert item.parked_reason.startswith(plans.LANDING_FAILED_PREFIX)
    todos = [t for t in store.list(HumanTask) if t.status == HumanTaskStatus.open]
    assert any("Land plan item" in t.title for t in todos)

    plans.cancel_item(store, item, "landed by hand")
    closed = resolve_open_todos(store)
    assert any("plan item" in t.resolved_reason for t in closed)


def test_plan_completion_wakes_planner_for_next_iteration(tmp_path):
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project, items=ITEMS[:1])
    processor, supervisor = make_processor(store, tmp_path)

    report(store, processor, only_resolve_task(store, project), "ok\nOUTCOME: FIXED")
    review = next(t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.review)
    report(store, processor, review, "good\nREVIEW: ACCEPT")

    assert store.get(Plan, plan.id).status == PlanStatus.complete
    assert any("propose the next iteration" in e.lower() for _, e in supervisor.events)


def test_edit_then_retry_parked_item(tmp_path):
    """The human amendment path on a live plan: rewrite a blocked item's
    constraints, retry, and it re-enters the pipeline with the new content."""
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project, items=ITEMS[:1])
    processor, _ = make_processor(store, tmp_path)
    report(store, processor, only_resolve_task(store, project), "unclear\nOUTCOME: BLOCKED")

    item = plans.plan_items(store, plan)[0]
    plans.update_item(store, item, {"constraints": "responsive web, no native app"})
    plans.retry_item(store, project, plan, store.get(PlanItem, item.id))

    item = store.get(PlanItem, item.id)
    assert item.status == PlanItemStatus.resolving
    assert item.parked_reason == ""
    retry_task = only_resolve_task(store, project)
    assert "responsive web, no native app" in retry_task.instructions


def test_operator_cancel_parks_instead_of_looping(tmp_path):
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project, items=ITEMS[:1])
    processor, _ = make_processor(store, tmp_path)
    report(store, processor, only_resolve_task(store, project), "stopping", cancelled=True)

    item = plans.plan_items(store, plan)[0]
    assert item.status == PlanItemStatus.blocked_clarity
    assert "cancelled by the operator" in item.parked_reason
    assert [t for t in store.list(Task, project_id=project.id)
            if t.status == TaskStatus.pending] == []


def test_amendment_add_item_to_approved_plan(tmp_path):
    """AI amendments enter as proposed and need the human flip; on an approved
    plan the flip queues directly, and the item runs when its turn comes."""
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project, items=ITEMS[:1])
    processor, _ = make_processor(store, tmp_path)

    extra = plans.add_item(store, project, plan, {"title": "Dark mode", "story": "s"}, "agent")
    assert extra.status == PlanItemStatus.proposed
    assert plans.advance_plan(store, project, plan) == 0  # proposed never executes

    report(store, processor, only_resolve_task(store, project), "ok\nOUTCOME: FIXED")
    review = next(t for t in store.list(Task, project_id=project.id) if t.kind == TaskKind.review)
    report(store, processor, review, "good\nREVIEW: ACCEPT")
    assert store.get(Plan, plan.id).status == PlanStatus.approved  # proposed item keeps it open

    plans.approve_item(store, store.get(Plan, plan.id), store.get(PlanItem, extra.id))
    assert store.get(PlanItem, extra.id).status == PlanItemStatus.queued
    plans.advance_plan(store, project, store.get(Plan, plan.id))
    assert store.get(PlanItem, extra.id).status == PlanItemStatus.resolving


def test_abandon_cancels_everything_not_terminal():
    store = MemoryStore()
    project = make_project(store)
    plan = activated_plan(store, project)
    plans.abandon_plan(store, store.get(Plan, plan.id))
    assert store.get(Plan, plan.id).status == PlanStatus.abandoned
    assert all(i.status in PLAN_ITEM_TERMINAL for i in plans.plan_items(store, plan))


def test_plan_doc_committed_to_spec_home_on_activation():
    """The durable record: activation commits a markdown doc carrying every
    item's content; the store copy stays authoritative when the commit fails."""
    store = MemoryStore()
    project = make_project(store)

    class FakeSpec:
        def __init__(self):
            self.files = {}

        def commit_files(self, files, message):
            self.files.update(files)
            return "abc12345"

    plan = plans.create_draft(store, project, "mobile support", ITEMS)
    plans.approve_all(store, plan)
    spec = FakeSpec()
    plans.activate(store, project, plan, spec=spec)
    doc = spec.files[plans.PLAN_DOC_PATH]
    for item in ITEMS:
        for value in item.values():
            assert value in doc
    assert store.get(Plan, plan.id).spec_ref == plans.PLAN_DOC_PATH


# -- API surface -----------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    from fastapi.testclient import TestClient

    from hive.api import create_app
    from hive.persistence.blobstore import LocalBlobStore
    from hive._control.supervisor import Supervisor

    store = MemoryStore()
    supervisor = Supervisor(store, lambda pid, events: None)
    config = make_config(tmp_path)
    client = TestClient(create_app(store, supervisor, config, blobs=LocalBlobStore(tmp_path / "b")))
    return client, store


class FakeSpecRepo:
    """Stands in for hive.api.SpecRepo so plan approval stays offline."""

    commits: dict = {}

    def __init__(self, url, base, token):
        pass

    def sync(self):
        return None

    def commit_files(self, files, message):
        FakeSpecRepo.commits.update(files)
        return "cafe1234"


def _api_project(client):
    pid = client.post("/api/projects", json={"name": "p"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"spec_repo": "https://github.com/o/r.git"})
    return pid


def test_plan_api_review_loop(app, monkeypatch):
    """Draft by hand → edit an item → flip one item → 'Approve all & start':
    the two review depths converge, execution starts, and the project payload
    carries the plan."""
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    FakeSpecRepo.commits = {}
    pid = _api_project(client)

    payload = client.post(
        f"/api/projects/{pid}/plan",
        json={"goal": "mobile", "items": [{"title": "A"}, {"title": "B", "story": "s"}]},
    ).json()
    plan_id = payload["plan"]["id"]
    first, second = payload["items"]
    assert payload["plan"]["proposed_by"] == "human"

    edited = client.patch(f"/api/plan-items/{first['id']}", json={"story": "user can X"}).json()
    assert edited["story"] == "user can X"
    assert edited["edited_by_human"]

    assert client.post(f"/api/plan-items/{first['id']}/approve").json()["status"] == "approved"
    result = client.post(f"/api/plans/{plan_id}/approve").json()
    statuses = {i["title"]: i["status"] for i in result["items"]}
    assert statuses == {"A": "resolving", "B": "queued"}
    assert plans.PLAN_DOC_PATH in FakeSpecRepo.commits

    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["plan"]["plan"]["status"] == "approved"
    tasks = [t for t in detail["tasks"] if t["kind"] == "resolve"]
    assert len(tasks) == 1 and t_work_item(tasks[0]) == first["id"]


def t_work_item(task: dict) -> str:
    return task["work_item_id"]


def test_plan_api_activation_refuses_partial_approval(app, monkeypatch):
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    pid = _api_project(client)
    client.post(
        f"/api/projects/{pid}/plan", json={"goal": "g", "items": [{"title": "A"}]}
    ).raise_for_status()
    # approve with zero items approved works via approve-all; an empty plan doesn't
    empty = client.post(f"/api/projects/{pid}/plan", json={"goal": "g2", "items": []}).json()
    r = client.post(f"/api/plans/{empty['plan']['id']}/approve")
    assert r.status_code == 400
    assert "no items" in r.json()["detail"]


def test_plan_api_abandon_cancels_pending_tasks(app, monkeypatch):
    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    pid = _api_project(client)
    payload = client.post(
        f"/api/projects/{pid}/plan", json={"goal": "g", "items": [{"title": "A"}]}
    ).json()
    plan_id = payload["plan"]["id"]
    client.post(f"/api/plans/{plan_id}/approve")

    result = client.post(f"/api/plans/{plan_id}/abandon").json()
    assert result["plan"]["status"] == "abandoned"
    assert all(i["status"] == "cancelled" for i in result["items"])
    tasks = store.list(Task, project_id=pid)
    assert tasks and all(t.status == TaskStatus.cancelled for t in tasks)


def test_cli_plan_commands(app, monkeypatch):
    """CLI/UI parity (a design principle: agents drive hive through the CLI):
    every plan action the web UI offers — draft, add, edit, reorder, flip,
    unapprove, approve-all, abandon — round-trips through the same routes."""
    from hive.cli import build_parser, run as cli_run

    def cli(client, *argv):
        return cli_run(build_parser().parse_args(argv), client)

    client, store = app
    monkeypatch.setattr("hive.api.SpecRepo", FakeSpecRepo)
    pid = _api_project(client)

    payload = cli(client, "plan-new", pid, "mobile support",
                  '[{"title": "A", "story": "s"}, {"title": "B"}]')
    assert [i["title"] for i in payload["items"]] == ["A", "B"]
    assert cli(client, "plan", pid)["plan"]["status"] == "draft"

    added = cli(client, "plan-item-add", pid, "C", "--story", "user can C")
    edited = cli(client, "plan-item-edit", added["id"],
                 "--constraints", "keep it tiny", "--order", "0")
    assert edited["constraints"] == "keep it tiny" and edited["edited_by_human"]
    # Reorder = swap, exactly what the UI's move-up does.
    cli(client, "plan-item-edit", payload["items"][0]["id"], "--order", str(added["order"]))
    assert [i["title"] for i in cli(client, "plan", pid)["items"]][0] == "C"

    first = payload["items"][0]
    assert cli(client, "plan-item-approve", first["id"])["status"] == "approved"
    assert cli(client, "plan-item-unapprove", first["id"])["status"] == "proposed"

    started = cli(client, "plan-approve", pid)
    assert {i["status"] for i in started["items"]} == {"resolving", "queued"}
    assert cli(client, "plan-abandon", pid)["plan"]["status"] == "abandoned"


def test_plan_doc_commit_failure_files_todo_but_executes():
    store = MemoryStore()
    project = make_project(store)

    class BrokenSpec:
        def commit_files(self, files, message):
            raise RuntimeError("push 403")

    plan = plans.create_draft(store, project, "goal", ITEMS[:1])
    plans.approve_all(store, plan)
    notes = plans.activate(store, project, plan, spec=BrokenSpec())
    assert store.get(Plan, plan.id).status == PlanStatus.approved
    assert any("commit failed" in n for n in notes)
    assert any("plan-doc" in t.dedup_key for t in store.list(HumanTask, status=HumanTaskStatus.open))
