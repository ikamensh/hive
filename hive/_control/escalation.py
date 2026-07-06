"""The 'ask a human for help' primitive and its self-closing lifecycle.

When Hive hits something only the operator can fix (a broken credential, a
failed merge, a dark machine) it files a `HumanTask`. Three rules keep the
todo board honest (ideal-ux G23–G26):

- **One todo per condition.** `escalate` is idempotent by `dedup_key` — a
  stable machine-made identity like `access:codex:hive-vm` shared by every
  producer — so a per-tick recurring failure or a differently-worded planner
  filing lands on one open todo, not a flood. Sites without a key fall back
  to exact (title, project) matching.
- **Evidence closes todos, not clicks.** A todo carries a `resolution`
  predicate naming the store fact that proves its condition is gone;
  `resolve_open_todos` sweeps them every supervisor step. Only todos without
  a predicate (`external` kind, mostly) rely on the human marking them done.
- **A todo names an action, not a question.** Information requests belong to
  `Question` / the planner's ask_user.

All predicates read store facts only — no network — so the sweep is safe to
run every tick.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from hive.models import (
    DEFAULT_WORKSPACE_ID,
    AgentConversation,
    ConversationStatus,
    Finding,
    FindingStatus,
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    Machine,
    OrchestratorRun,
    Project,
    Resource,
    ResourceUsability,
    Runner,
    Story,
    StoryStatus,
    Task,
    TaskStatus,
    Workstream,
    WorkstreamStatus,
)

log = logging.getLogger("hive._control.escalation")


def escalate(
    store,
    title: str,
    instructions: str,
    project_id: str = "",
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    *,
    kind: HumanTaskKind = HumanTaskKind.external,
    dedup_key: str = "",
    resolution: dict | None = None,
) -> HumanTask | None:
    """File an operator todo unless one for the same condition is already open
    (same `dedup_key`, or same (title, scope) as the fallback). `project_id=""`
    is an org-wide todo. Returns the task it filed, or None if a matching one
    was already open."""
    for task in store.list(HumanTask, workspace_id=workspace_id, status=HumanTaskStatus.open):
        if dedup_key and task.dedup_key == dedup_key:
            return None
        if task.title == title and task.project_id == project_id:
            return None
    return store.put(
        HumanTask(
            workspace_id=workspace_id,
            project_id=project_id,
            title=title,
            instructions=instructions,
            kind=kind,
            dedup_key=dedup_key,
            resolution=resolution or {},
        )
    )


def resolve_todo(store, workspace_id: str, dedup_key: str, reason: str) -> None:
    """Event-driven close: a producer observed its condition resolved. Use when
    the proof is not a plain store fact the sweep could check (e.g. a successful
    test-refresh finalization)."""
    for task in store.list(HumanTask, workspace_id=workspace_id, status=HumanTaskStatus.open):
        if task.dedup_key == dedup_key:
            _close(store, task, reason)


def _close(store, todo: HumanTask, reason: str) -> None:
    todo.status = HumanTaskStatus.done
    todo.done_at = time.time()
    todo.resolved_reason = reason
    store.put(todo)
    log.info("auto-resolved human todo %s '%s': %s", todo.id, todo.title, reason)


# -- resolution predicates ----------------------------------------------------
# Each takes (store, todo) and returns the evidence sentence when the todo's
# condition no longer holds, or "" while it persists. A predicate whose subject
# vanished from the store resolves as stale — a todo about a deleted object is
# unactionable by definition.


def _resource_usable(store, todo: HumanTask) -> str:
    backend = todo.resolution.get("backend", "")
    name = todo.resolution.get("runner_name", "")
    runners = [r for r in store.list(Runner, workspace_id=todo.workspace_id) if r.name == name]
    if not runners:
        return f"runner `{name}` is no longer registered"
    resources = [
        res
        for runner in runners
        for res in store.list(
            Resource, workspace_id=todo.workspace_id, runner_id=runner.id, backend=backend
        )
    ]
    for res in resources:
        if res.enabled and res.usability_status == ResourceUsability.usable:
            return f"`{backend}` probed usable on `{name}`"
    if resources and not any(res.enabled for res in resources):
        return f"the operator disabled `{backend}` on `{name}`"
    return ""


def _machine_online(store, todo: HumanTask) -> str:
    name = todo.resolution.get("machine_name", "")
    machines = [m for m in store.list(Machine, workspace_id=todo.workspace_id) if m.name == name]
    if not machines:
        return f"machine `{name}` is no longer registered"
    for machine in machines:
        if machine.last_seen > todo.created_at:
            return f"machine `{name}` heartbeated again"
    return ""


def _task_inactive(store, todo: HumanTask) -> str:
    task = store.get(Task, todo.resolution.get("task_id", ""))
    if task is None:
        return "the task no longer exists"
    if task.status not in (TaskStatus.pending, TaskStatus.running):
        return f"the task is {task.status}"
    return ""


def _story_verdict(store, todo: HumanTask) -> str:
    story = store.get(Story, todo.resolution.get("story_id", ""))
    if story is None:
        return "the story no longer exists"
    if story.status in (StoryStatus.passing, StoryStatus.failing):
        return f"story `{story.key}` reached a verdict: {story.status}"
    return ""


def _finding_decided(store, todo: HumanTask) -> str:
    finding = store.get(Finding, todo.resolution.get("finding_id", ""))
    if finding is None:
        return "the finding no longer exists"
    if finding.status != FindingStatus.blocked:
        return f"the finding was decided: {finding.status}"
    return ""


def _workstream_done(store, todo: HumanTask) -> str:
    ws = store.get(Workstream, todo.resolution.get("workstream_id", ""))
    if ws is None:
        return "the work item no longer exists"
    if ws.status == WorkstreamStatus.done:
        return "the work item landed"
    return ""


def _conversation_done(store, todo: HumanTask) -> str:
    conv = store.get(AgentConversation, todo.resolution.get("conversation_id", ""))
    if conv is None:
        return "the conversation no longer exists"
    if conv.status == ConversationStatus.done:
        return "the intake conversation finalized"
    return ""


def _orchestrator_ran(store, todo: HumanTask) -> str:
    runs = store.list(
        OrchestratorRun,
        workspace_id=todo.workspace_id,
        project_id=todo.resolution.get("project_id", ""),
    )
    if any(run.created_at > todo.created_at for run in runs):
        return "the planner completed an invocation after this was filed"
    return ""


def _project_state_not(store, todo: HumanTask) -> str:
    project = store.get(Project, todo.resolution.get("project_id", ""))
    if project is None:
        return "the project no longer exists"
    state = todo.resolution.get("state", "")
    if project.state != state:
        return f"the project left {state} (now {project.state})"
    return ""


RESOLUTION_CHECKS: dict[str, Callable[[object, HumanTask], str]] = {
    "resource_usable": _resource_usable,
    "machine_online": _machine_online,
    "task_inactive": _task_inactive,
    "story_verdict": _story_verdict,
    "finding_decided": _finding_decided,
    "workstream_done": _workstream_done,
    "conversation_done": _conversation_done,
    "orchestrator_ran": _orchestrator_ran,
    "project_state_not": _project_state_not,
}


def resolve_open_todos(store, workspace_id: str = DEFAULT_WORKSPACE_ID) -> list[HumanTask]:
    """Close every open todo whose resolution predicate holds, recording the
    evidence in `resolved_reason`. Returns what it closed."""
    resolved = []
    for todo in store.list(HumanTask, workspace_id=workspace_id, status=HumanTaskStatus.open):
        check = RESOLUTION_CHECKS.get(todo.resolution.get("check", ""))
        if check is None:
            continue
        reason = check(store, todo)
        if reason:
            _close(store, todo, reason)
            resolved.append(todo)
    return resolved
