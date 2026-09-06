"""Versioned execution results derived from the plan's existing ledger.

These documents describe delivered work, not acceptance of the higher project
goal. Exporting a result never requires an LLM or changes execution state.
"""

import time
from typing import Literal

from pydantic import BaseModel

from hive.models import (
    PLAN_ITEM_PARKED, HumanTask, HumanTaskStatus, Plan, PlanItemStatus, PlanStatus,
    Project, Question, QuestionStatus, Task, TaskKind, TaskStatus, ValidationResult, Verdict,
)
from hive._workstreams.plans import plan_items


class ResultCounts(BaseModel):
    total: int
    done: int
    cancelled: int
    remaining: int


class AttemptResult(BaseModel):
    task_id: str
    plan_revision: int
    kind: TaskKind
    status: TaskStatus
    backend: str
    model: str
    runner_id: str
    retry_of_task_id: str
    result_text: str
    verdict: Verdict
    is_error: bool
    incomplete_reason: str
    structured_result: dict
    structured_result_error: str
    validation: ValidationResult | None
    trace_blob: str
    artifact_blobs: list[str]
    prompt_versions: dict[str, str]
    cost_usd: float
    input_tokens: int
    output_tokens: int
    created_at: float
    started_at: float
    finished_at: float


class ItemResult(BaseModel):
    id: str
    title: str
    order: int
    repo: str
    status: PlanItemStatus
    parked_reason: str
    merged_commit_sha: str
    merged_at: float
    attempts: list[AttemptResult]


class PlanResult(BaseModel):
    schema_version: Literal[1] = 1
    kind: Literal["plan_result"] = "plan_result"
    project_id: str
    plan_id: str
    revision: int
    input_digest: str
    goal: str
    status: PlanStatus
    terminal: bool
    outcome: Literal["pending", "running", "blocked", "stopping", "done", "partial", "abandoned"]
    counts: ResultCounts
    items: list[ItemResult]
    questions: list[Question]
    todos: list[HumanTask]
    captured_at: float
    created_at: float
    approved_at: float
    finished_at: float


def result_document(store, project: Project, plan: Plan) -> dict:
    """Export a JSON-ready execution snapshot, preserving item order and facts."""
    items = plan_items(store, plan)
    tasks = sorted(store.list(
        Task, workspace_id=project.workspace_id, project_id=project.id, run_id=plan.id,
    ), key=lambda task: (task.created_at, task.id))
    item_ids = {item.id for item in items}
    references = {"plan_id": {plan.id}, "plan_item_id": item_ids,
                  "workstream_id": item_ids, "task_id": {task.id for task in tasks}}
    questions = [question for question in store.list(
        Question, workspace_id=project.workspace_id, project_id=project.id, status=QuestionStatus.open,
    ) if not question.workstream_id or question.workstream_id in item_ids]
    todos = [todo for todo in store.list(
        HumanTask, workspace_id=project.workspace_id, project_id=project.id, status=HumanTaskStatus.open,
    ) if all(key not in todo.resolution or todo.resolution[key] in ids
             for key, ids in references.items())]
    done = sum(item.status == PlanItemStatus.done for item in items)
    cancelled = sum(item.status == PlanItemStatus.cancelled for item in items)
    live_attempts = any(task.status in (TaskStatus.pending, TaskStatus.running) for task in tasks)
    terminal = plan.status in (PlanStatus.complete, PlanStatus.abandoned) and not live_attempts
    if terminal:
        outcome = "abandoned" if plan.status == PlanStatus.abandoned else "partial" if cancelled else "done"
    elif plan.status == PlanStatus.abandoned:
        outcome = "stopping"
    elif any(item.status in PLAN_ITEM_PARKED for item in items):
        outcome = "blocked"
    else:
        outcome = "pending" if plan.status == PlanStatus.draft else "running"
    return PlanResult(
        project_id=project.id,
        plan_id=plan.id,
        revision=plan.revision,
        input_digest=plan.input_digest,
        goal=plan.goal,
        status=plan.status,
        terminal=terminal,
        outcome=outcome,
        counts=ResultCounts(total=len(items), done=done, cancelled=cancelled, remaining=len(items) - done - cancelled),
        items=[ItemResult(
            id=item.id, title=item.title, order=item.order, repo=item.repo or project.spec_repo,
            status=item.status, parked_reason=item.parked_reason,
            merged_commit_sha=item.merged_commit_sha, merged_at=item.merged_at,
            attempts=[AttemptResult(task_id=task.id, **task.model_dump())
                      for task in tasks if task.work_item_id == item.id],
        ) for item in items],
        questions=questions,
        todos=todos,
        captured_at=time.time(),
        created_at=plan.created_at,
        approved_at=plan.approved_at,
        finished_at=max(plan.finished_at, *(task.finished_at for task in tasks), 0) if terminal else 0,
    ).model_dump(mode="json")
