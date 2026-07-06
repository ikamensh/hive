"""The planner acts on Hive itself instead of delegating to the human (G25):
cancel_task stops its own stuck work, withdraw_question retracts moot
questions, and create_human_task is guarded so planner-authored todos carry a
kind, share the system's dedup keys, and get server-side login recipes rather
than LLM-remembered commands.

Properties verified:
- cancel_task honors the operator-cancel transition (pending stops outright,
  delivered running work is flagged for cooperative stop) and refuses tasks
  it does not own (other projects, deterministic pipeline kinds);
- a withdrawn question stops gating mark_goal_complete without human action;
- planner and system filings for the same condition collapse onto one todo;
- an access todo is org-wide, auto-closing, and carries the registry recipe.
"""

from hive.models import (
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    Project,
    Question,
    QuestionStatus,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskKind,
    TaskStatus,
    Workstream,
    WorkstreamStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.escalation import escalate, resolve_open_todos
from hive._control.orchestrator import Tools


def _tools(store):
    project = store.put(Project(name="p", spec_repo="x"))
    return Tools(store, project, spec=None), project


def _task(store, project, **kwargs):
    defaults = dict(project_id=project.id, workstream_id="w", repo="r", instructions="i")
    defaults.update(kwargs)
    return store.put(Task(**defaults))


# -- cancel_task ----------------------------------------------------------------


def test_cancel_pending_task_stops_it_outright():
    store = MemoryStore()
    tools, project = _tools(store)
    task = _task(store, project)

    assert tools.cancel_task(task.id, "backend is dead") == "cancelled"
    task = store.get(Task, task.id)
    assert task.status == TaskStatus.cancelled
    assert "backend is dead" in task.result_text
    assert task.finished_at > 0


def test_cancel_delivered_running_task_is_cooperative():
    store = MemoryStore()
    tools, project = _tools(store)
    task = _task(store, project, status=TaskStatus.running, delivered=True)

    assert "runner will stop it" in tools.cancel_task(task.id, "obsolete")
    task = store.get(Task, task.id)
    assert task.status == TaskStatus.running and task.cancel_requested


def test_cancel_refuses_foreign_and_pipeline_tasks():
    store = MemoryStore()
    tools, project = _tools(store)
    other = store.put(Project(name="other", spec_repo="y"))
    foreign = _task(store, other)
    pipeline = _task(store, project, kind=TaskKind.resolve)
    finished = _task(store, project, status=TaskStatus.done)

    assert "error" in tools.cancel_task(foreign.id, "r")
    assert "error" in tools.cancel_task(pipeline.id, "r")
    assert "error" in tools.cancel_task(finished.id, "r")
    assert store.get(Task, foreign.id).status == TaskStatus.pending


# -- withdraw_question ------------------------------------------------------------


def test_withdrawn_question_unblocks_completion():
    """The live failure mode: the planner asked 'Acknowledged, proceed?'-style
    questions, then filed todos begging the human to answer them because open
    questions gate mark_goal_complete. Withdrawing must clear the gate."""
    store = MemoryStore()
    tools, project = _tools(store)
    ws_id = tools.create_workstream("w", "d").split("=")[1]
    q_id = tools.ask_user("Context. Options: a/b. Recommendation: a.").split("=")[1].split()[0]

    ws = store.get(Workstream, ws_id)
    ws.status = WorkstreamStatus.done
    store.put(ws)
    _task(
        store, project, workstream_id=ws_id, kind=TaskKind.verify,
        status=TaskStatus.done, verdict="accept",
    )
    assert "open questions" in tools.mark_goal_complete("done. Try it: run x")

    out = tools.withdraw_question(q_id, "answered by the spec meanwhile")
    assert out == "withdrawn"
    q = store.get(Question, q_id)
    assert q.status == QuestionStatus.withdrawn
    assert "answered by the spec meanwhile" in q.answer
    assert tools.mark_goal_complete("done. Try it: run x") == "goal marked complete"


def test_withdraw_names_workstreams_still_parked_on_the_question():
    store = MemoryStore()
    tools, _project = _tools(store)
    ws_id = tools.create_workstream("w", "d").split("=")[1]
    q_id = tools.ask_user("Ctx. Options: a/b. Recommendation: a.", ws_id).split("=")[1].split()[0]
    assert store.get(Workstream, ws_id).status == WorkstreamStatus.parked

    out = tools.withdraw_question(q_id, "moot")
    assert ws_id in out and "parked" in out


def test_withdraw_refuses_foreign_or_settled_questions():
    store = MemoryStore()
    tools, project = _tools(store)
    other = store.put(
        Question(project_id="someone-else", text="q", status=QuestionStatus.open)
    )
    answered = store.put(
        Question(project_id=project.id, text="q", status=QuestionStatus.answered)
    )
    assert "error" in tools.withdraw_question(other.id, "r")
    assert "error" in tools.withdraw_question(answered.id, "r")


# -- guarded create_human_task -----------------------------------------------------


def test_planner_todo_requires_a_known_kind():
    store = MemoryStore()
    tools, _ = _tools(store)
    assert "error" in tools.create_human_task("t", "i", kind="decision")
    assert "error" in tools.create_human_task("t", "i", kind="access")  # no backend/machine


def test_planner_access_todo_shares_the_system_dedup_key():
    """One root cause, one todo: a probe-failure escalation and a planner
    filing for the same (backend, machine) collapse."""
    store = MemoryStore()
    tools, _ = _tools(store)
    escalate(
        store, "Fix codex login on hive-vm", "x",
        kind=HumanTaskKind.access,
        dedup_key="access:codex:hive-vm",
        resolution={"check": "resource_usable", "backend": "codex", "runner_name": "hive-vm"},
    )
    out = tools.create_human_task(
        "Log in to ChatGPT subscription on hive-vm", "please log in",
        kind="access", backend="codex", machine="hive-vm",
    )
    assert "error" in out and "already covers" in out
    assert len(store.list(HumanTask, status=HumanTaskStatus.open)) == 1


def test_planner_access_todo_is_org_wide_recipe_carrying_and_self_closing():
    store = MemoryStore()
    tools, _ = _tools(store)
    out = tools.create_human_task(
        "Fix codex login on hive-vm", "the scout hit an auth wall",
        kind="access", backend="codex", machine="hive-vm",
    )
    assert out.startswith("human_task_id=")
    (todo,) = store.list(HumanTask)
    assert todo.project_id == ""  # forced org-wide: a login serves the fleet
    assert todo.kind == HumanTaskKind.access
    assert "codex login" in todo.instructions  # registry recipe, not LLM memory

    # The condition resolving closes it without a human click.
    runner = store.put(Runner(name="hive-vm", backends=["codex"]))
    store.put(
        Resource(
            runner_id=runner.id, backend="codex",
            usability_status=ResourceUsability.usable,
        )
    )
    resolve_open_todos(store)
    assert store.get(HumanTask, todo.id).status == HumanTaskStatus.done
