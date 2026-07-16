"""Transient backend flakes must not kill work.

Live regression (droid-tally demo, 2026-07-16): gemini-cli died three times
with "Invalid stream: The model returned an empty response or malformed tool
call". Each one hard-failed its task — failing the intake conversation (a
fresh scout loses the brief) or parking the plan item until a human ran
plan-retry. The property under test: a failure that `classify_failure` calls
"transient" requeues the same task (bounded by TRANSIENT_RETRY_LIMIT) and is
invisible to the owning workflow — conversations stay running, plan items stay
resolving — while anything past the budget, or any non-transient failure,
lands exactly as before.

Everything runs the real TaskResultProcessor against MemoryStore: no network,
no LLM. Tests loop on TRANSIENT_RETRY_LIMIT rather than hardcoding attempt
counts, so retuning the constant does not rewrite them.
"""

from hive.config.settings import Config
from hive.models import (
    AgentConversation,
    ConversationStatus,
    HumanTask,
    Plan,
    PlanItemStatus,
    Project,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive.runner._task_results import TRANSIENT_RETRY_LIMIT, TaskResult, TaskResultProcessor
from hive.runner.registration import queue_probe
from hive._control import intake
from hive._workstreams import plans

FLAKE = "Invalid stream: The model returned an empty response or malformed tool call."


class SupervisorRecorder:
    def __init__(self):
        self.events = []
        self.pokes = 0

    def wake(self, project_id, event):
        self.events.append((project_id, event))

    def poke(self):
        self.pokes += 1


def make_processor(store, tmp_path):
    supervisor = SupervisorRecorder()
    processor = TaskResultProcessor(
        store,
        supervisor,
        Config(gcp_project="", gcs_bucket="", gh_token="t", gemini_api_key="",
               orch_model="", runner_token="test-token", data_dir=tmp_path),
        merge_branch_func=lambda repo, head, token, message="": None,
        resolve_issue_func=lambda *a, **k: None,
        delete_branch_func=lambda repo, branch, token: None,
    )
    return processor, supervisor


def claim(store, task: Task, runner_id: str = "r-1") -> None:
    """Simulate dispatch: the pending task starts running on a runner."""

    def mark(saved: Task) -> None:
        saved.status = TaskStatus.running
        saved.runner_id = saved.runner_id or runner_id
        saved.delivered = True

    store.update(Task, task.id, mark)


def make_plan_resolve_task(store) -> tuple[Project, Task]:
    """A real plan pipeline up to its first resolve task, like test_plans does."""
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    plan = plans.create_draft(
        store, project, "goal", [{"title": "One item", "story": "s", "constraints": ""}]
    )
    plans.approve_all(store, plan)
    plans.activate(store, project, plan)
    (task,) = [t for t in store.list(Task, project_id=project.id)]
    return project, task


def make_intake_turn(store) -> tuple[Project, AgentConversation, Task]:
    project = store.put(Project(name="demo", spec_repo="https://github.com/o/r.git"))
    conversation = store.put(
        AgentConversation(
            workspace_id=project.workspace_id,
            project_id=project.id,
            repo=project.spec_repo,
            backend="gemini-cli",
            model="gemini-3.1-pro-preview",
        )
    )
    project.intake_conversation_id = conversation.id
    store.put(project)
    task = intake.queue_turn(store, project, conversation, "initial")
    return project, store.get(AgentConversation, conversation.id), task


def run_until_final(store, processor, task: Task, **result_kwargs) -> int:
    """Keep failing the task transiently until the processor stops requeueing;
    returns how many attempts ran."""
    attempts = 0
    while True:
        claim(store, store.get(Task, task.id))
        attempts += 1
        outcome = processor.handle(
            task.id, TaskResult(text=FLAKE, is_error=True, **result_kwargs), task.workspace_id
        )
        if not outcome.get("requeued"):
            return attempts


# -- requeue semantics ----------------------------------------------------------


def test_transient_failure_requeues_instead_of_failing():
    """One flake returns the task to the dispatch queue: pending, unassigned,
    undelivered — the exact shape `_requeue_dropped_work` proves re-dispatchable
    — with the flake kept visible as the interim result text."""
    store = MemoryStore()
    processor, supervisor = make_processor(store, "/tmp")
    project, task = make_plan_resolve_task(store)
    claim(store, task)

    outcome = processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)

    saved = store.get(Task, task.id)
    assert outcome == {"ok": True, "requeued": True, "transient_retries": 1}
    assert saved.status == TaskStatus.pending
    assert saved.runner_id == ""
    assert saved.delivered is False
    assert saved.transient_retries == 1
    assert FLAKE in saved.result_text
    # The retry is the supervisor's business (poke), never the planner's (wake).
    assert supervisor.pokes == 1
    assert supervisor.events == []


def test_requeued_plan_item_stays_resolving():
    """The owning workflow must not observe the flake: the plan item is still
    'work in flight', not parked for a human."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, task = make_plan_resolve_task(store)
    claim(store, task)

    processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)

    (item,) = plans.plan_items(store, store.list(Plan, project_id=project.id)[0])
    assert item.status == PlanItemStatus.resolving


def test_retry_budget_exhausts_then_fails_for_real():
    """Exactly TRANSIENT_RETRY_LIMIT requeues are granted; the next transient
    failure lands as a real one (plan item parks with the error, task fails)."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, task = make_plan_resolve_task(store)

    attempts = run_until_final(store, processor, task)

    assert attempts == TRANSIENT_RETRY_LIMIT + 1
    saved = store.get(Task, task.id)
    assert saved.status == TaskStatus.failed
    assert saved.transient_retries == TRANSIENT_RETRY_LIMIT
    (item,) = plans.plan_items(store, store.list(Plan, project_id=project.id)[0])
    assert item.status == PlanItemStatus.blocked_clarity
    assert FLAKE in item.parked_reason


def test_non_transient_error_fails_immediately():
    """A plain failure is a verdict, not a flake: no requeue, no retry spend."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, task = make_plan_resolve_task(store)
    claim(store, task)

    processor.handle(
        task.id, TaskResult(text="AssertionError: expected 2 got 3", is_error=True), task.workspace_id
    )

    saved = store.get(Task, task.id)
    assert saved.status == TaskStatus.failed
    assert saved.transient_retries == 0


def test_operator_and_capacity_signals_beat_transient_text():
    """A cancel, an auth block, or exhaustion wins even when the text reads
    like a flake — retrying a cancelled task or a dead credential would loop."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")

    for kwargs, expected_status in (
        ({"cancelled": True}, TaskStatus.cancelled),
        ({"auth_blocked": True}, TaskStatus.failed),
        ({"resource_exhausted": True}, TaskStatus.failed),
    ):
        project, task = make_plan_resolve_task(store)
        claim(store, task)
        processor.handle(
            task.id, TaskResult(text=FLAKE, is_error=True, **kwargs), task.workspace_id
        )
        saved = store.get(Task, task.id)
        assert saved.status == expected_status, kwargs
        assert saved.transient_retries == 0, kwargs


def test_cancel_requested_task_is_not_resurrected():
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, task = make_plan_resolve_task(store)
    claim(store, task)
    store.update(Task, task.id, lambda t: setattr(t, "cancel_requested", True))

    processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)

    assert store.get(Task, task.id).status == TaskStatus.failed


def test_duplicate_result_post_requeues_once():
    """Posting the same transient result twice is idempotent: the second post
    sees a non-running task and is ignored."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, task = make_plan_resolve_task(store)
    claim(store, task)

    first = processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)
    second = processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)

    assert first.get("requeued") is True
    assert second.get("ignored") is True
    assert store.get(Task, task.id).transient_retries == 1


# -- intake conversations --------------------------------------------------------


def test_intake_turn_retries_same_conversation_instead_of_failing():
    """The conversation is untouched by a flaky turn: still running, no
    assistant error in the transcript, and `intake.start` keeps returning the
    same conversation (the pending retry counts as the live turn) instead of
    minting a fresh scout that would lose the brief."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, conversation, task = make_intake_turn(store)
    claim(store, task)

    processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)

    saved_conv = store.get(AgentConversation, conversation.id)
    assert saved_conv.status == ConversationStatus.running
    assert all(entry.get("role") != "assistant" for entry in saved_conv.transcript)
    assert store.get(Task, task.id).status == TaskStatus.pending
    assert intake.start(store, store.get(Project, project.id)).id == conversation.id
    assert store.get(Project, project.id).state == "intake"


def test_intake_conversation_fails_only_after_retry_budget():
    """Past the budget the old behavior returns: conversation failed, operator
    todo filed — but only once the flake has proven persistent."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, conversation, task = make_intake_turn(store)

    attempts = run_until_final(store, processor, task)

    assert attempts == TRANSIENT_RETRY_LIMIT + 1
    assert store.get(AgentConversation, conversation.id).status == ConversationStatus.failed
    todos = [t for t in store.list(HumanTask) if t.title.startswith("Intake scout failed")]
    assert len(todos) == 1


# -- probes -----------------------------------------------------------------------


def test_probe_flake_redelivers_without_a_usability_verdict():
    """A flaky probe re-runs on its own runner (probes never ride the
    dispatcher) and the resource keeps its 'probing' status — a one-off stream
    death must not brand the backend failed and block dispatch/intake on it."""
    store = MemoryStore()
    processor, supervisor = make_processor(store, "/tmp")
    runner = store.put(Runner(name="box", backends=["gemini-cli"]))
    resource = store.put(
        Resource(machine_id="m-1", runner_id=runner.id, backend="gemini-cli")
    )
    task, resource = queue_probe(store, resource, runner)
    store.update(Task, task.id, lambda t: setattr(t, "delivered", True))

    outcome = processor.handle(task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id)

    saved = store.get(Task, task.id)
    assert outcome.get("requeued") is True
    assert saved.status == TaskStatus.running  # still pinned to its runner
    assert saved.runner_id == runner.id
    assert saved.delivered is False  # the next poll re-delivers it
    saved_resource = store.get(Resource, resource.id)
    assert saved_resource.usability_status == ResourceUsability.probing
    assert saved_resource.total_tasks == 1  # the attempt's spend still counts
    assert supervisor.pokes == 0  # nothing to dispatch


def test_probe_past_budget_fails_resource_as_before():
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    runner = store.put(Runner(name="box", backends=["gemini-cli"]))
    resource = store.put(
        Resource(machine_id="m-1", runner_id=runner.id, backend="gemini-cli")
    )
    task, resource = queue_probe(store, resource, runner)

    attempts = 0
    while True:
        attempts += 1
        outcome = processor.handle(
            task.id, TaskResult(text=FLAKE, is_error=True), task.workspace_id
        )
        if not outcome.get("requeued"):
            break

    assert attempts == TRANSIENT_RETRY_LIMIT + 1
    assert store.get(Task, task.id).status == TaskStatus.failed
    saved_resource = store.get(Resource, resource.id)
    assert saved_resource.usability_status == ResourceUsability.failed
    assert saved_resource.total_tasks == TRANSIENT_RETRY_LIMIT + 1


# -- spend accounting --------------------------------------------------------------


def test_spend_accumulates_across_attempts():
    """Budgets see the whole cost of retried work: the task row and the
    resource both carry the sum of every attempt, failed and final alike."""
    store = MemoryStore()
    processor, _ = make_processor(store, "/tmp")
    project, conversation, task = make_intake_turn(store)
    runner = store.put(Runner(name="box", backends=["gemini-cli"]))
    resource = store.put(
        Resource(machine_id="m-1", runner_id="r-1", backend="gemini-cli")
    )

    costs = [round(0.5 * (i + 1), 2) for i in range(TRANSIENT_RETRY_LIMIT)]  # failed attempts
    for cost in costs:
        claim(store, store.get(Task, task.id))
        outcome = processor.handle(
            task.id,
            TaskResult(text=FLAKE, is_error=True, cost_usd=cost, input_tokens=10, output_tokens=5),
            task.workspace_id,
        )
        assert outcome.get("requeued") is True
    claim(store, store.get(Task, task.id))
    processor.handle(
        task.id,
        TaskResult(text="Here is the brief.", cost_usd=2.0, input_tokens=10, output_tokens=5),
        task.workspace_id,
    )

    saved = store.get(Task, task.id)
    total = sum(costs) + 2.0
    assert saved.status == TaskStatus.done
    assert saved.cost_usd == total
    assert saved.input_tokens == 10 * (TRANSIENT_RETRY_LIMIT + 1)
    assert saved.output_tokens == 5 * (TRANSIENT_RETRY_LIMIT + 1)
    assert store.get(Resource, resource.id).total_cost_usd == total
    assert store.get(AgentConversation, conversation.id).status == ConversationStatus.open
