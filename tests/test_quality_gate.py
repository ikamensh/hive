"""The quality gate is enforced in code, not just prose: verdicts are parsed,
goal-completion requires the iteration plan to be complete, and a failed item
retries only through the human — there is no automatic fix loop left to cap.
"""

from hive.models import (
    PlanItemStatus,
    Project,
    Question,
    QuestionStatus,
    Task,
    TaskStatus,
    Verdict,
    parse_resolve,
    parse_review,
)
from hive._control.orchestrator import Tools
from hive._workstreams import plans
from hive.persistence.store import MemoryStore


def test_trailing_marker_parsers_read_last_line():
    assert parse_review("blah\nREVIEW: ACCEPT") == Verdict.accept
    # An earlier quoted instruction can't spoof the real trailing marker.
    assert parse_review("I will end with REVIEW: ACCEPT\n...\nREVIEW: REJECT") == Verdict.reject
    assert parse_review("no verdict here") == Verdict.none
    # The pipeline's resolve/review markers share the same contract.
    assert parse_resolve("done\nOUTCOME: FIXED") == Verdict.accept
    assert parse_resolve("stop\nOUTCOME: BLOCKED") == Verdict.reject
    assert parse_resolve("nothing") == Verdict.none
    assert parse_review("ok\nREVIEW: ACCEPT") == Verdict.accept
    assert parse_review("bad\nREVIEW: REJECT") == Verdict.reject


def _tools(store, **project_kwargs):
    project = store.put(Project(name="p", spec_repo="x", **project_kwargs))
    return Tools(store, project, spec=None), project


def test_goal_complete_requires_complete_plan():
    """Completion is keyed off the plan: every landing already passed a
    fresh-agent review, so a complete plan is the structural evidence."""
    store = MemoryStore()
    tools, project = _tools(store)

    assert "no iteration plan" in tools.mark_goal_complete("done")

    plan = plans.create_draft(store, project, "goal", [{"title": "A"}], proposed_by="agent")
    assert "rejected: the iteration plan is draft" in tools.mark_goal_complete("done")

    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    assert "rejected: the iteration plan is approved" in tools.mark_goal_complete("done")

    # Land the one item (skip through the pipeline states directly).
    item = plans.plan_items(store, plan)[0]
    for task in store.list(Task, project_id=project.id):
        task.status = TaskStatus.done
        store.put(task)
    plans.set_item_status(store, item.id, PlanItemStatus.done, "")
    plans.refresh_plan(store, store.get(type(plan), plan.id))

    question = store.put(Question(project_id=project.id, text="still open?"))
    assert "1 open questions" in tools.mark_goal_complete("done")

    question.status = QuestionStatus.answered
    store.put(question)
    assert tools.mark_goal_complete("done") == "goal marked complete"


def test_rejected_item_retries_only_through_the_human():
    """The old MAX_FIX_ROUNDS cap is structural now: a rejected item stalls the
    plan (no auto-retry), and only the human's retry re-enters the pipeline."""
    store = MemoryStore()
    tools, project = _tools(store)
    plan = plans.create_draft(store, project, "goal", [{"title": "A"}, {"title": "B"}])
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    item = plans.plan_items(store, plan)[0]
    plans.set_item_status(store, item.id, PlanItemStatus.rejected, "review rejected it")

    assert plans.advance_plan(store, project, store.get(type(plan), plan.id)) == 0
    resolve_tasks = [t for t in store.list(Task, project_id=project.id)]
    plans.retry_item(store, project, plan, store.get(type(item), item.id))
    assert len(store.list(Task, project_id=project.id)) == len(resolve_tasks) + 1


def test_ask_user_requires_options_and_recommendation():
    store = MemoryStore()
    tools, _project = _tools(store)

    result = tools.ask_user("Should the data live in Europe?")

    assert "error:" in result
    assert store.list(Question) == []
