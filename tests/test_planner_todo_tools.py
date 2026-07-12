"""The planner acts on Hive itself instead of delegating to the human (G25):
withdraw_question retracts moot questions, and create_human_task is guarded so
planner-authored todos carry a kind, share the system's dedup keys, and get
server-side login recipes rather than LLM-remembered commands. (The old
cancel_task tool went with planner-queued build tasks: the pipeline owns
execution now, and the operator cancels through the API.)

Properties verified:
- a withdrawn question stops gating mark_goal_complete without human action;
- planner and system filings for the same condition collapse onto one todo;
- an access todo is org-wide, auto-closing, and carries the registry recipe.
"""

from hive.models import (
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    Plan,
    PlanItemStatus,
    Project,
    Question,
    QuestionStatus,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.escalation import escalate, resolve_open_todos
from hive._control.orchestrator import Tools
from hive._workstreams import plans


def _tools(store):
    project = store.put(Project(name="p", spec_repo="x"))
    return Tools(store, project, spec=None), project


def _completed_plan(store, project):
    plan = plans.create_draft(store, project, "goal", [{"title": "A"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    item = plans.plan_items(store, plan)[0]
    plans.set_item_status(store, item.id, PlanItemStatus.done, "")
    # Drop the resolve task activation queued: the item is landed.
    for task in store.list(Task, project_id=project.id):
        task.status = TaskStatus.cancelled
        store.put(task)
    return plans.refresh_plan(store, store.get(Plan, plan.id))


# -- withdraw_question ------------------------------------------------------------


def test_withdrawn_question_unblocks_completion():
    """The live failure mode: the planner asked 'Acknowledged, proceed?'-style
    questions, then filed todos begging the human to answer them because open
    questions gate mark_goal_complete. Withdrawing must clear the gate."""
    store = MemoryStore()
    tools, project = _tools(store)
    _completed_plan(store, project)
    q_id = tools.ask_user("Context. Options: a/b. Recommendation: a.").split("=")[1].split()[0]

    assert "open questions" in tools.mark_goal_complete("done. Try it: run x")

    out = tools.withdraw_question(q_id, "answered by the spec meanwhile")
    assert out == "withdrawn"
    q = store.get(Question, q_id)
    assert q.status == QuestionStatus.withdrawn
    assert "answered by the spec meanwhile" in q.answer
    assert tools.mark_goal_complete("done. Try it: run x") == "goal marked complete"


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
