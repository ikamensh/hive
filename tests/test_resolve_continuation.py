"""Unfinished builds are durable work, not unanswered product questions."""

import pytest
from pydantic import ValidationError

from hive.agents import ResultSpec, call_agent
from hive.runner._agent_results import ResolveResult
from hive.models import PlanItemStatus, Task, TaskKind, TaskStatus, ValidationResult
from hive.persistence.store import FileStore
from hive.runner._task_results import TaskResult
from hive._workstreams import plans
from test_agent_results import FakeAgent, FakeResult, _write_result
from test_plans import activated_plan, make_processor, make_project, only_resolve_task
from test_transient_retry import claim


def test_report_repair_can_report_unfinished_work_without_inventing_a_blocker(tmp_path):
    """A partial build survives report repair; the schema can honestly say
    incomplete while the repair call remains restricted to reporting."""
    def build(directory, instructions):
        assert '"incomplete"' in instructions
        (directory / "partial.py").write_text("# work in progress\n")
        result = FakeResult("Assignment logic updated — testing shared demand.")
        result.incomplete_reason = "Tool loop ended after a native permission refusal."
        return result

    def report(directory, instructions):
        assert "Only create or repair" in instructions
        assert "not itself a blocker" in instructions
        assert "Tool loop ended after a native permission refusal." in instructions
        _write_result(directory, {
            "task_id": "build-1", "outcome": "incomplete",
            "summary": "Assignment logic implemented; verification remains.",
            "remaining_work": "Finish shared-demand tests, then commit and push.",
        })
        return FakeResult("Implementation remains unfinished.")

    result = call_agent(FakeAgent([build, report]), instructions="Build the approved item.",
                        workdir=tmp_path, result_spec=ResultSpec(ResolveResult, repair_attempts=1),
                        task_id="build-1")

    assert result.structured_result["outcome"] == "incomplete"
    assert result.structured_result_error == ""
    assert result.attempts == 2
    assert result.incomplete_reason == "Tool loop ended after a native permission refusal."
    assert (tmp_path / "partial.py").read_text() == "# work in progress\n"


def test_blocked_result_requires_an_actual_question_for_the_owner():
    """The live false-blocked report cannot validate without identifying a
    decision; a real owner question can still stop work immediately."""
    with pytest.raises(ValidationError, match="blocking_question"):
        ResolveResult(task_id="build-1", outcome="blocked",
                      summary="Result file was missing so reporting blocked with no branch push.")
    result = ResolveResult(task_id="build-1", outcome="blocked",
                           blocking_question="Should dispatch prefer earliest deadline or lowest cost?")
    assert result.outcome == "blocked"


@pytest.mark.parametrize("report_kind", ["incomplete", "missing", "invalid_blocked"])
def test_unfinished_build_continuations_survive_restart_and_stop_after_two(tmp_path, report_kind):
    """The same branch/session survives two bounded successors and restart;
    malformed or missing reports cannot land or become product clarification."""
    store_path = tmp_path / "store"
    store = FileStore(store_path)
    project = make_project(store)
    plan = activated_plan(store, project)
    original = only_resolve_task(store, project)
    original_records = {}

    for attempt in range(3):
        task = only_resolve_task(store, project)
        claim(store, task, "runner-with-edits")
        processor, _ = make_processor(store, tmp_path)
        payload = {"task_id": task.id, "outcome": "incomplete",
                   "remaining_work": "Finish shared-demand tests, then commit and push."}
        error = ""
        if report_kind == "missing":
            payload, error = {}, ".hive/result.json was not created"
        elif report_kind == "invalid_blocked":
            payload = {"task_id": task.id, "outcome": "blocked", "summary": "No result file."}
        response = processor.handle(task.id, TaskResult(
            text="Last model commentary, not an implementation verdict.",
            structured_result=payload, structured_result_error=error,
            session_handle="warm-session", input_tokens=10,
        ), task.workspace_id)
        finished = store.get(Task, task.id)
        assert finished.status == TaskStatus.failed
        original_records[task.id] = finished.model_dump()
        # Replayed delivery cannot spend another continuation or duplicate a task.
        assert processor.handle(task.id, TaskResult(text="late report"), task.workspace_id)["ignored"]
        store = FileStore(store_path)
        for task_id, recorded in original_records.items():
            assert store.get(Task, task_id).model_dump() == recorded
        assert not store.list(Task, kind=TaskKind.review)
        first, second, _ = plans.plan_items(store, plan)
        assert second.status == PlanItemStatus.queued
        if attempt < 2:
            assert response["requeued"]
            successor = only_resolve_task(store, project)
            assert successor.retry_of_task_id == task.id
            assert successor.branch == original.branch
            assert successor.preserve_checkout and not successor.fresh_branch
            assert successor.resume_runner_id == "runner-with-edits"
            assert successor.session_handle == "warm-session"
            assert successor.continuation_attempts == attempt + 1
            assert successor.input_tokens == 0
            assert first.status == PlanItemStatus.resolving
            assert "Continue the existing task" in successor.instructions
        else:
            assert not store.list(Task, status=TaskStatus.pending)
            assert first.status == PlanItemStatus.rejected
            assert "2 continuation attempts" in first.parked_reason
            assert "retry" in first.parked_reason.lower()


@pytest.mark.parametrize("missing_evidence", ["invalid_report", "unfinished_transport", "explicit_incomplete"])
def test_unfinished_review_continues_review_and_cannot_land_from_legacy_accept(tmp_path, missing_evidence):
    """An incomplete review stays in its own fresh review session and needs
    both a completed review report and executable validation before landing."""
    store = FileStore(tmp_path / "store")
    project = make_project(store)
    project.validation_command = "make check"
    store.put(project)
    plan = activated_plan(store, project)
    merged = []
    processor, _ = make_processor(store, tmp_path, merge=lambda *args, **kw: merged.append(args[1]))
    build = only_resolve_task(store, project)
    claim(store, build)
    processor.handle(build.id, TaskResult(text="Built.", session_handle="builder-session",
        structured_result={"task_id": build.id, "outcome": "fixed", "branch_pushed": True}), "default")
    review = store.list(Task, kind=TaskKind.review)[0]
    assert not review.session_handle
    claim(store, review)
    kwargs = {
        "invalid_report": {"structured_result_error": ".hive/result.json was not created"},
        "unfinished_transport": {"incomplete_reason": "No terminal response after native tool calls."},
        "explicit_incomplete": {"structured_result": {"task_id": review.id, "outcome": "incomplete",
                                                       "remaining_work": "Finish the independent boundary checks."}},
    }[missing_evidence]
    response = processor.handle(review.id, TaskResult(
        text="REVIEW: ACCEPT", session_handle="fresh-review-session",
        validation=ValidationResult(command="make check", exit_code=0, commit_sha="a" * 40),
        **kwargs), "default")
    successor = store.get(Task, response["retry_task_id"])
    assert successor.kind == TaskKind.review
    assert successor.session_handle == "fresh-review-session"
    assert successor.session_handle != "builder-session"
    assert successor.branch == review.branch and successor.preserve_checkout
    assert successor.validation is None
    assert plans.plan_items(store, plan)[0].status == PlanItemStatus.reviewing
    assert not merged
    claim(store, successor)
    processor.handle(successor.id, TaskResult(text="Review complete.",
        structured_result={"task_id": successor.id, "outcome": "accept"},
        validation=ValidationResult(command="make check", exit_code=0, commit_sha="b" * 40)), "default")
    assert merged == ["b" * 40]
    assert plans.plan_items(store, plan)[0].status == PlanItemStatus.done
