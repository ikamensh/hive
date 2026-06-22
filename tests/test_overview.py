"""Home-dashboard overview builder.

Properties: the overview is a faithful single-pass rollup of the store — its
totals agree with the per-project rows and the underlying records, archived
projects drop out, and capacity grouping reflects which agents are actually
dispatchable. These survive refactors because they assert relationships, not
hand-counted constants.
"""

import time

from hive.control.overview import agent_status, build_overview
from hive.models import (
    HumanTask,
    Machine,
    Project,
    Question,
    QuestionStatus,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskStatus,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
)
from hive.persistence.store import MemoryStore

WS = "default"


def _spend_zero(_pid: str) -> float:
    return 0.0


def test_empty_store_has_shape_and_zero_totals():
    """Executability: an empty workspace still returns every section so the
    client never has to guard against missing keys."""
    ov = build_overview(MemoryStore(), WS, _spend_zero)

    assert ov["projects"] == []
    assert ov["live_tasks"] == []
    assert ov["subscriptions"] == []
    assert ov["attention"]["count"] == 0
    assert ov["capacity"]["machines"] == []
    totals = ov["totals"]
    assert totals == {
        "tasks_running": 0,
        "agents_ready": 0,
        "agents_total": 0,
        "machines_online": 0,
        "machines_total": 0,
        "needs_you": 0,
        "spend_today": 0.0,
        "budget_today": 0.0,
    }


def test_totals_agree_with_rows_and_records():
    store = MemoryStore()
    live = store.put(Project(workspace_id=WS, name="atlas", daily_budget_usd=40))
    store.put(Project(workspace_id=WS, name="ghost", archived=True))

    store.put(Workstream(workspace_id=WS, project_id=live.id, title="build a", status=WorkstreamStatus.active))
    store.put(
        Workstream(
            workspace_id=WS,
            project_id=live.id,
            title="issue 7",
            source=WorkstreamSource.issue,
            status=WorkstreamStatus.blocked_clarity,
        )
    )
    store.put(
        Task(
            workspace_id=WS,
            project_id=live.id,
            workstream_id="w",
            repo="r",
            instructions="go",
            backend="claude",
            status=TaskStatus.running,
            started_at=time.time(),
        )
    )
    store.put(Question(workspace_id=WS, project_id=live.id, text="which db?"))
    store.put(HumanTask(workspace_id=WS, project_id="", title="log into gemini", instructions="..."))

    ov = build_overview(store, WS, lambda pid: 5.0 if pid == live.id else 0.0)

    # Archived projects never reach the dashboard.
    assert [p["name"] for p in ov["projects"]] == ["atlas"]
    row = ov["projects"][0]
    assert row["counts"] == {"active": 1, "running": 1, "questions": 1, "blockers": 1, "streams": 0}
    assert row["spend_today"] == 5.0

    totals = ov["totals"]
    # tasks_running rolls up the per-project running counts.
    assert totals["tasks_running"] == sum(p["counts"]["running"] for p in ov["projects"])
    # needs_you is exactly the open questions plus open human todos.
    assert totals["needs_you"] == 2
    assert ov["attention"]["count"] == 2
    assert totals["spend_today"] == 5.0
    assert totals["budget_today"] == 40

    # The running task surfaces in live_tasks with its project name resolved.
    assert [t["project_name"] for t in ov["live_tasks"]] == ["atlas"]


def test_capacity_groups_agents_under_machine_and_marks_readiness():
    store = MemoryStore()
    machine = store.put(Machine(workspace_id=WS, name="mac-studio", device_kind="server"))
    runner = store.put(
        Runner(workspace_id=WS, machine_id=machine.id, name="mac", backends=["claude", "gemini-cli"])
    )
    store.put(
        Resource(
            workspace_id=WS,
            machine_id=machine.id,
            runner_id=runner.id,
            backend="claude",
            usability_status=ResourceUsability.usable,
        )
    )
    store.put(
        Resource(
            workspace_id=WS,
            machine_id=machine.id,
            runner_id=runner.id,
            backend="gemini-cli",
            usability_status=ResourceUsability.usable,
            cooldown_until=time.time() + 600,
        )
    )

    cap = build_overview(store, WS, _spend_zero)["capacity"]

    assert cap["machines_total"] == 1
    assert cap["machines_online"] == 1  # runner.last_seen defaults to now
    card = cap["machines"][0]
    assert card["name"] == "mac-studio" and card["online"]
    by_backend = {a["backend"]: a for a in card["agents"]}
    assert by_backend["claude"]["status"] == "ready" and by_backend["claude"]["available"]
    assert by_backend["gemini-cli"]["status"] == "cooldown" and not by_backend["gemini-cli"]["available"]

    # ready never exceeds total, and only the dispatchable agent counts as ready.
    assert cap["agents_total"] == 2
    assert cap["agents_ready"] == 1
    assert cap["agents_ready"] <= cap["agents_total"]


def test_agent_status_reflects_runner_and_enable_state():
    """The single readiness word degrades sensibly as conditions worsen."""
    offline_runner = Runner(name="old", backends=["claude"], last_seen=time.time() - 10_000)
    online_runner = Runner(name="now", backends=["claude"])

    usable = Resource(runner_id=online_runner.id, backend="claude", usability_status=ResourceUsability.usable)
    assert agent_status(usable, online_runner) == "ready"
    assert agent_status(usable, offline_runner) == "offline"
    assert agent_status(usable, None) == "offline"

    disabled = Resource(runner_id=online_runner.id, backend="claude", enabled=False, usability_status=ResourceUsability.usable)
    assert agent_status(disabled, online_runner) == "disabled"

    unknown = Resource(runner_id=online_runner.id, backend="claude")
    assert agent_status(unknown, online_runner) == "probe"
