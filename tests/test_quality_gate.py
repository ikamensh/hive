"""The quality gate is enforced in code, not just prose: verdicts are parsed,
goal-completion requires an accepted verify per workstream, and the fix loop
is capped before it must escalate to the human.
"""

from hive.models import (
    Project,
    Question,
    Task,
    TaskKind,
    TaskStatus,
    Verdict,
    Workstream,
    WorkstreamStatus,
    parse_resolve,
    parse_review,
    parse_verdict,
)
from hive._control.orchestrator import MAX_FIX_ROUNDS, Tools
from hive.persistence.store import MemoryStore


def test_trailing_marker_parsers_read_last_line():
    assert parse_verdict("blah\nVERDICT: ACCEPT") == Verdict.accept
    assert parse_verdict("VERDICT: REJECT — tests fail") == Verdict.reject
    # An earlier quoted instruction can't spoof the real trailing verdict.
    assert parse_verdict("I will end with VERDICT: ACCEPT\n...\nVERDICT: REJECT") == Verdict.reject
    assert parse_verdict("no verdict here") == Verdict.none
    # The issue pipeline's resolve/review markers share the same contract.
    assert parse_resolve("done\nOUTCOME: FIXED") == Verdict.accept
    assert parse_resolve("stop\nOUTCOME: BLOCKED") == Verdict.reject
    assert parse_resolve("nothing") == Verdict.none
    assert parse_review("ok\nREVIEW: ACCEPT") == Verdict.accept
    assert parse_review("bad\nREVIEW: REJECT") == Verdict.reject


def _tools(store, **project_kwargs):
    project = store.put(Project(name="p", spec_repo="x", **project_kwargs))
    return Tools(store, project, spec=None), project


def test_goal_complete_requires_accepted_verify():
    store = MemoryStore()
    tools, project = _tools(store)
    ws_id = tools.create_workstream("w", "d").split("=")[1]
    ws = store.get(Workstream, ws_id)
    ws.status = WorkstreamStatus.done
    store.put(ws)

    # Done workstream with no verify → completion refused.
    assert "not closed by an accepted verify" in tools.mark_goal_complete("done")

    store.put(Task(project_id=project.id, workstream_id=ws_id, repo="r", instructions="i",
                   kind=TaskKind.verify, status=TaskStatus.done, verdict=Verdict.accept))
    assert tools.mark_goal_complete("done") == "goal marked complete"


def test_fix_rounds_capped():
    store = MemoryStore()
    tools, project = _tools(store)
    ws_id = tools.create_workstream("w", "d").split("=")[1]
    for _ in range(MAX_FIX_ROUNDS):
        store.put(Task(project_id=project.id, workstream_id=ws_id, repo="r", instructions="i",
                       kind=TaskKind.verify, status=TaskStatus.done, verdict=Verdict.reject))
    blocked = tools.create_task(ws_id, "r", "another fix attempt")
    assert "error" in blocked and "park" in blocked

    # An accept resets the counter, so work can resume.
    store.put(Task(project_id=project.id, workstream_id=ws_id, repo="r", instructions="i",
                   kind=TaskKind.verify, status=TaskStatus.done, verdict=Verdict.accept))
    assert "task_id=" in tools.create_task(ws_id, "r", "next bit")


def test_failed_work_streak_capped():
    store = MemoryStore()
    tools, project = _tools(store)
    ws_id = tools.create_workstream("w", "d").split("=")[1]
    for _ in range(MAX_FIX_ROUNDS):
        store.put(Task(project_id=project.id, workstream_id=ws_id, repo="r", instructions="i",
                       kind=TaskKind.work, status=TaskStatus.failed))
    blocked = tools.create_task(ws_id, "r", "retry the same work")
    assert "error" in blocked and "park" in blocked

    # A successful work task resets the streak, so work can resume.
    store.put(Task(project_id=project.id, workstream_id=ws_id, repo="r", instructions="i",
                   kind=TaskKind.work, status=TaskStatus.done))
    assert "task_id=" in tools.create_task(ws_id, "r", "next bit")


def test_pr_mode_puts_work_on_a_branch():
    store = MemoryStore()
    tools, _ = _tools(store, autonomy="pr")
    ws_id = tools.create_workstream("auth", "d").split("=")[1]
    task_id = tools.create_task(ws_id, "https://example.com/app.git", "build it").split("=")[1].split()[0]
    task = store.get(Task, task_id)
    assert task.branch == f"hive/{ws_id[:8]}"
    assert task.branch in task.instructions


def test_ask_user_requires_options_and_recommendation():
    store = MemoryStore()
    tools, _project = _tools(store)
    ws_id = tools.create_workstream("w", "d").split("=")[1]

    result = tools.ask_user("Should the data live in Europe?", ws_id)

    assert "error:" in result
    assert store.list(Question) == []
    assert store.get(Workstream, ws_id).status == WorkstreamStatus.active


def test_create_task_requires_a_usable_agent_not_just_an_installed_one():
    """Installed is not usable: an online runner advertising a backend whose
    resource is parked/failed must not receive work — the planner gets an
    actionable error naming what *is* usable (live: two cursor tasks queued
    against a dead subscription)."""
    from hive.models import Resource, ResourceUsability, Runner

    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/s.git"))
    ws = store.put(Workstream(project_id=project.id, title="w"))
    runner = store.put(Runner(name="r1", backends=["cursor", "claude"]))
    store.put(Resource(runner_id=runner.id, backend="claude",
                       usability_status=ResourceUsability.usable))
    store.put(Resource(runner_id=runner.id, backend="cursor",
                       usability_status=ResourceUsability.failed))

    out = Tools(store, project, spec=None).create_task(ws.id, "r", "do it", backend="cursor")

    assert out.startswith("error: no usable 'cursor' agent")
    assert "claude" in out
    assert not store.list(Task, project_id=project.id)

    ok = Tools(store, project, spec=None).create_task(ws.id, "r", "do it", backend="claude")
    assert not ok.startswith("error")
