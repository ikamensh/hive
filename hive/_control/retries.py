"""Recover interrupted work as immutable attempts with durable owner references."""

from __future__ import annotations

import time
import uuid
import threading

from hive.models import (
    PLAN_ITEM_IN_FLIGHT, AgentConversation, ConversationStatus, Finding, PlanItem,
    Resource, Task, TaskKind, TaskStatus, Verdict,
)

_RETRY_LOCK = threading.RLock()


def resume_interrupted_task(store, task: Task) -> Task | None:
    """Create one successor, retaining checkout/session but never the previous attempt's ledger.

    The deterministic ID and conditional owner-link updates recover a chief
    crash between recording failure and creating/linking the successor.
    """
    # Result handlers and the supervisor recovery sweep share one chief.
    # Serialize creation so neither can overwrite a successor the other started.
    with _RETRY_LOCK:
        return _resume(store, task)


def _resume(store, task: Task) -> Task | None:
    task = store.get(Task, task.id)
    if task is None or task.status != TaskStatus.failed or not task.retryable_interruption or task.cancel_requested:
        return None
    retry_id = uuid.uuid5(uuid.NAMESPACE_URL, f"hive-retry:{task.id}").hex[:12]
    item = store.get(PlanItem, task.work_item_id) if task.work_item_id else None
    if item and item.status not in PLAN_ITEM_IN_FLIGHT:
        return None
    conversation = store.get(AgentConversation, task.conversation_id) if task.conversation_id else None
    if conversation and (conversation.status not in (ConversationStatus.running, ConversationStatus.finalizing)
                         or conversation.last_task_id not in (task.id, retry_id)):
        return None
    successor = store.get(Task, retry_id)
    if successor is None:
        probe = task.kind == TaskKind.probe
        successor = store.put(Task(**{
            **task.model_dump(),
            "id": retry_id, "retry_of_task_id": task.id,
            "status": TaskStatus.running if probe else TaskStatus.pending,
            "runner_id": task.runner_id if probe else "", "delivered": False,
            "fresh_branch": False, "preserve_checkout": not probe,
            "resume_runner_id": task.runner_id or task.resume_runner_id,
            "retryable_interruption": False, "dispatch_reason": "",
            "validation": None, "verdict": Verdict.none, "trace_blob": "", "artifact_blobs": [],
            "result_text": "", "is_error": False, "cost_usd": 0,
            "input_tokens": 0, "output_tokens": 0,
            "structured_result": {}, "structured_result_error": "",
            "created_at": time.time(), "started_at": time.time() if probe else 0, "finished_at": 0,
        }))
    for model, field in (
        (AgentConversation, "last_task_id"), (Resource, "last_probe_task_id"),
        (Finding, "confirm_task_id"),
    ):
        for owner in store.list(model, workspace_id=task.workspace_id, **{field: task.id}):
            def relink(saved, field=field):
                if getattr(saved, field) == task.id:
                    setattr(saved, field, successor.id)
            store.update(model, owner.id, relink)
    return successor
