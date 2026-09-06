"""The overview HTTP snapshot reports observed execution, capacity and landings."""

import time

from test_plans import app as app

from hive.models import (
    Machine, Plan, PlanItem, PlanItemStatus, PlanStatus, Project, Resource, ResourceUsability, Runner,
    Story, Task, TaskKind, TaskStatus, UsageWindow, ValidationResult,
)


def test_overview_locates_work_and_preserves_lost_heartbeats_while_paused(app):
    """Paused work can still be running; a silent runner must not look healthy."""
    client, store = app
    project = store.put(Project(name="Exports"))
    plan = store.put(Plan(project_id=project.id, goal="Export CSV", status=PlanStatus.approved))
    item = store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="Quote CSV rows",
                              status=PlanItemStatus.resolving))
    machine = store.put(Machine(name="Laptop", device_kind="laptop"))
    runner = store.put(Runner(name="Laptop runner", machine_id=machine.id,
                              backends=["codex"], last_seen=time.time() - 600))
    task = store.put(Task(project_id=project.id, workstream_id=item.id, work_item_id=item.id,
                          run_id=plan.id, repo="https://github.com/acme/csv", instructions="Private prompt",
                          kind=TaskKind.resolve, backend="codex", runner_id=runner.id,
                          status=TaskStatus.running, started_at=time.time() - 700, delivered=True))
    store.put(Task(project_id=project.id, workstream_id="", repo=task.repo, instructions="Next"))
    store.put(Task(workspace_id="other", project_id=project.id, workstream_id="", repo=task.repo,
                   instructions="Foreign pending work"))
    client.patch("/api/workspace", json={"paused": True}).raise_for_status()

    before = time.time()
    snapshot = client.get("/api/overview").raise_for_status().json()

    assert before <= snapshot["observed_at"] <= time.time()
    assert snapshot["paused"] and snapshot["totals"]["tasks_running"] == 1
    assert snapshot["tasks_pending"] == 1
    live = snapshot["live_tasks"][0]
    assert live["id"] == task.id and live["title"] == item.title
    assert live["project_name"] == project.name and live["backend"] == "codex"
    assert (live["plan_id"], live["work_item_id"]) == (plan.id, item.id)
    assert (live["runner_id"], live["runner_name"]) == (runner.id, runner.name)
    assert (live["machine_id"], live["machine_name"]) == (machine.id, machine.name)
    assert not live["runner_online"] and live["runner_last_seen"] == runner.last_seen
    assert live["delivered"] and live["repo"] == task.repo
    assert "Private prompt" not in str(snapshot)
    assert snapshot["execution"] == {"working": 0, "starting": 0, "disconnected": 1}

    online = store.put(Runner(name="Online runner", backends=["codex"]))
    for delivered in (False, True):
        store.put(Task(project_id=project.id, workstream_id="", repo=task.repo, instructions="Work",
                       runner_id=online.id, status=TaskStatus.running, delivered=delivered))
    mixed = client.get("/api/overview").raise_for_status().json()
    assert mixed["execution"] == {"working": 1, "starting": 1, "disconnected": 1}
    assert sum(mixed["execution"].values()) == mixed["totals"]["tasks_running"]


def test_overview_counts_idle_runners_instead_of_backend_choices_or_preview_rows(app):
    """Busy work beyond the preview still occupies runners; installed CLIs add no slots."""
    client, store = app
    project = store.put(Project(name="Exports"))
    machine = store.put(Machine(name="Build host"))
    busy = []
    for index in range(40):
        runner = store.put(Runner(name=f"Busy {index}", machine_id=machine.id, backends=["codex"]))
        busy.append(runner)
        store.put(Resource(runner_id=runner.id, backend="codex", usability_status=ResourceUsability.usable))
        store.put(Task(project_id=project.id, workstream_id="", repo="r", instructions="Work",
                       runner_id=runner.id, status=TaskStatus.running))
    idle = store.put(Runner(name="Spare", machine_id=machine.id, backends=["codex", "claude"]))
    for backend in idle.backends:
        store.put(Resource(runner_id=idle.id, backend=backend, usability_status=ResourceUsability.usable))
    unprobed = store.put(Runner(name="Unprobed", machine_id=machine.id, backends=["codex"]))
    store.put(Resource(runner_id=unprobed.id, backend="codex"))
    offline = store.put(Runner(name="Offline", machine_id=machine.id, backends=["codex"], last_seen=1))
    store.put(Resource(runner_id=offline.id, backend="codex", usability_status=ResourceUsability.usable))
    store.put(Runner(workspace_id="other", name="Foreign runner", backends=["codex"]))

    snapshot = client.get("/api/overview").raise_for_status().json()
    capacity = snapshot["capacity"]

    assert len(snapshot["live_tasks"]) < len(busy)
    assert snapshot["execution"] == {"working": 0, "starting": len(busy), "disconnected": 0}
    assert capacity["runners_total"] == len(busy) + 3
    assert capacity["runners_online"] == len(busy) + 2
    assert capacity["runners_busy"] == len(busy)
    assert capacity["runners_usable"] == len(busy) + 1
    assert capacity["runners_idle"] == 1
    assert capacity["agents_ready"] > capacity["runners_usable"]
    for key in ("runners_total", "runners_online", "runners_busy", "runners_usable", "runners_idle"):
        assert capacity[key] == sum(card[key] for card in capacity["machines"])


def test_recent_deliveries_require_landing_and_commit_specific_validation(app):
    """Completed tasks are not deliveries; a passing check for another SHA is not green evidence."""
    client, store = app
    project = store.put(Project(name="Exports", spec_repo="https://github.com/acme/csv"))
    plan = store.put(Plan(project_id=project.id, goal="Export CSV", status=PlanStatus.complete))
    landed = "a" * 40
    other_commit = "b" * 40
    items = []
    for index, (status, checked_sha, exit_code) in enumerate([
        ("not_recorded", "", 0), ("different_commit", other_commit, 0),
        ("failed", landed, 1), ("passed", landed, 0),
    ]):
        item = store.put(PlanItem(project_id=project.id, plan_id=plan.id, title=status,
                                  status=PlanItemStatus.done, merged_at=index + 1,
                                  merged_commit_sha=landed))
        items.append(item)
        if checked_sha:
            store.put(Task(project_id=project.id, workstream_id=item.id, work_item_id=item.id,
                           run_id=plan.id, repo=project.spec_repo, instructions="Review",
                           kind=TaskKind.review, status=TaskStatus.done, finished_at=index + 1,
                           validation=ValidationResult(command="make check", exit_code=exit_code,
                                                       commit_sha=checked_sha)))
    store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="Done without receipt",
                       status=PlanItemStatus.done))
    store.put(PlanItem(project_id=project.id, plan_id=plan.id, title="Cancelled",
                       status=PlanItemStatus.cancelled, merged_commit_sha=landed, merged_at=20))
    store.put(PlanItem(workspace_id="other", project_id=project.id, plan_id=plan.id, title="Foreign",
                       status=PlanItemStatus.done, merged_commit_sha=landed, merged_at=20))
    store.put(Task(project_id=project.id, workstream_id="", repo=project.spec_repo,
                   instructions="Task completed without shipping", status=TaskStatus.done))

    deliveries = client.get("/api/overview").raise_for_status().json()["recent_deliveries"]

    assert deliveries["count"] == len(items)
    assert [row["work_item_id"] for row in deliveries["items"]] == [item.id for item in reversed(items)]
    for row in deliveries["items"]:
        assert row["project_name"] == project.name and row["project_id"] == project.id
        assert row["plan_id"] == plan.id and row["repo"] == project.spec_repo
        assert row["merged_commit_sha"] == landed and row["merged_at"] > 0
        assert row["validation"]["status"] == row["title"]
    assert deliveries["items"][0]["validation"]["commit_sha"] == landed
    assert deliveries["items"][1]["validation"]["command"] == "make check"
    assert deliveries["items"][2]["validation"]["commit_sha"] == other_commit


def test_delivery_preview_stays_bounded_and_links_to_the_iteration_that_landed(app):
    """A new draft cannot erase delivered history; the count includes rows beyond the preview."""
    client, store = app
    project = store.put(Project(name="Exports"))
    old = store.put(Plan(project_id=project.id, goal="Earlier delivery", status=PlanStatus.complete))
    store.put(Plan(project_id=project.id, goal="Next draft"))
    for index in range(40):
        store.put(PlanItem(project_id=project.id, plan_id=old.id, title=f"Delivered {index}",
                           status=PlanItemStatus.done, merged_commit_sha="a" * 40, merged_at=index + 1))
    archived = store.put(Project(name="Archived", archived=True))
    store.put(PlanItem(project_id=archived.id, plan_id="old", title="Archived receipt",
                       status=PlanItemStatus.done, merged_commit_sha="a" * 40, merged_at=100))

    deliveries = client.get("/api/overview").raise_for_status().json()["recent_deliveries"]

    assert deliveries["count"] == 40
    assert 0 < len(deliveries["items"]) < deliveries["count"]
    assert [row["merged_at"] for row in deliveries["items"]] == sorted(
        (row["merged_at"] for row in deliveries["items"]), reverse=True)
    assert deliveries["items"][0]["title"] == "Delivered 39"
    assert all(row["plan_id"] == old.id for row in deliveries["items"])


def test_live_testing_work_uses_its_story_title_without_claiming_a_plan(app):
    """Testing episodes have run IDs too; the dashboard labels their story, not an invented plan."""
    client, store = app
    project = store.put(Project(name="Exports"))
    story = store.put(Story(project_id=project.id, workstream_id="tests", key="csv", title="Export Unicode rows"))
    task = store.put(Task(project_id=project.id, workstream_id=story.id, work_item_id=story.id,
                          run_id="test-episode", repo="r", instructions="Internal agent instructions",
                          status=TaskStatus.running, kind=TaskKind.test_sweep))

    live = client.get("/api/overview").raise_for_status().json()["live_tasks"][0]

    assert live["id"] == task.id and live["title"] == story.title
    assert live["work_item_id"] == story.id and live["plan_id"] == ""
    assert live["runner_id"] == "" and not live["runner_online"]


def test_overview_keeps_provider_gauges_separate_and_preserves_missing_data(app):
    """Account-wide readings on two hosts are observations, never additive quota."""
    client, store = app
    captured = time.time() - 100
    reset = time.time() + 600
    for name in ("Laptop", "Server"):
        machine = store.put(Machine(name=name))
        runner = store.put(Runner(name=name, machine_id=machine.id, backends=["codex", "cursor"]))
        store.put(Resource(runner_id=runner.id, backend="codex", usability_status=ResourceUsability.usable,
                           usage_plan="plus", usage_source="rollout", usage_captured_at=captured,
                           usage_windows=[UsageWindow(kind="weekly", used_percent=35, resets_at=reset)],
                           model_cooldowns={"model-specific": reset}))
        store.put(Resource(runner_id=runner.id, backend="cursor", usability_status=ResourceUsability.usable))

    snapshot = client.get("/api/overview").raise_for_status().json()

    assert len(snapshot["capacity"]["machines"]) == 2
    for machine in snapshot["capacity"]["machines"]:
        gauges = {agent["backend"]: agent for agent in machine["agents"]}
        codex = gauges["codex"]
        assert codex["usage_captured_at"] == captured
        assert (codex["usage_plan"], codex["usage_source"]) == ("plus", "rollout")
        assert codex["usage_windows"][0]["used_percent"] == 35
        assert codex["usage_windows"][0]["resets_at"] == reset
        assert codex["model_cooldowns"] == {"model-specific": reset}
        assert gauges["cursor"]["usage_windows"] == []
        assert gauges["cursor"]["usage_captured_at"] == 0


def test_archived_projects_keep_their_identity_while_running_work_drains(app):
    """Archiving hides the portfolio row, not the identity of already running work."""
    client, store = app
    project = store.put(Project(name="Archived exports", archived=True))
    task = store.put(Task(project_id=project.id, workstream_id="", repo="r", instructions="Finish",
                          kind=TaskKind.resolve, status=TaskStatus.running))

    snapshot = client.get("/api/overview").raise_for_status().json()

    assert snapshot["projects"] == []
    assert snapshot["totals"]["tasks_running"] == 1
    assert snapshot["live_tasks"][0]["id"] == task.id
    assert snapshot["live_tasks"][0]["project_name"] == project.name
