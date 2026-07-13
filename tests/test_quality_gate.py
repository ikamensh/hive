"""The quality gate is enforced in code, not just prose: verdicts are parsed,
goal-completion requires the iteration plan to be complete, and a failed item
retries only through the human — there is no automatic fix loop left to cap.
"""

from hive.models import (
    Plan,
    PlanItemStatus,
    PlanStatus,
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


def test_propose_plan_refused_while_completed_plan_awaits_goal_verdict():
    """Live-run regression: after the plan completed, the planner drafted a
    self-invented next iteration instead of declaring the goal. The next
    iteration is the human's verdict — propose_plan must refuse until
    mark_goal_complete (or a human-set goal) resolves the completed plan."""
    from hive._workstreams import plans

    store = MemoryStore()
    project = store.put(Project(name="p", spec_repo="https://example.com/r.git"))
    plan = plans.create_draft(store, project, "ship v1", [{"title": "build it"}], proposed_by="agent")
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    (item,) = plans.plan_items(store, plan)
    plans.set_item_status(store, item.id, PlanItemStatus.done, "")
    plans.refresh_plan(store, store.get(Plan, plan.id))
    assert store.get(Plan, plan.id).status == PlanStatus.complete

    tools = Tools(store, store.get(Project, project.id), spec=None)
    answer = tools.propose_plan("improve usability", '[{"title": "polish"}]')
    assert "rejected" in answer and "mark_goal_complete" in answer

    # Observed live: a drafted-then-abandoned invention must not lift the
    # guard — the completed plan still awaits its verdict behind it.
    draft = plans.create_draft(store, project, "invented", [{"title": "x"}], proposed_by="agent")
    plans.abandon_plan(store, draft)
    answer = tools.propose_plan("improve usability again", '[{"title": "y"}]')
    assert "rejected" in answer

    # ...and symmetrically, the abandoned draft must not BLOCK the verdict:
    # mark_goal_complete judges the completed plan, not the abandoned one.
    # (activate() queued a resolve task for the done item; finish it first —
    # the quiescence rule is a separate, correct gate.)
    from hive.models import TaskStatus

    for t in store.list(Task, project_id=project.id):
        t.status = TaskStatus.done
        store.put(t)
    verdict = tools.mark_goal_complete("Shipped. Try it: python3 tally.py report")
    assert verdict == "goal marked complete"

    # After the goal verdict, planning for a human-set goal reopens.
    project = store.get(Project, project.id)
    project.goal_complete = True
    store.put(project)
    tools = Tools(store, project, spec=None)
    answer = tools.propose_plan("next goal from the human", '[{"title": "next"}]')
    assert "drafted" in answer
