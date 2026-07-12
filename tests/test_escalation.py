"""HumanTask lifecycle: idempotent filing and evidence-based auto-resolution.

Properties verified (see hive/_control/escalation.py, ideal-ux G23-G26):
- `escalate` files one todo per condition: dedup by `dedup_key` beats wording
  (two differently-titled filings for the same key collapse), and the
  (title, project) fallback still guards keyless sites;
- a *closed* todo does not block refiling — a recurring condition gets a
  fresh todo per episode;
- every resolution predicate closes its todo exactly when the store fact it
  names flips, records the evidence in `resolved_reason`, and resolves as
  stale when its subject vanished from the store;
- todos without a predicate (external kind) are never auto-closed.
"""

import time

from hive.models import (
    AgentConversation,
    ConversationStatus,
    Finding,
    FindingStatus,
    HumanTask,
    HumanTaskKind,
    HumanTaskStatus,
    OrchestratorRun,
    Project,
    ProjectState,
    Story,
    StoryStatus,
    Task,
    TaskStatus,
    IssueItem,
    IssueItemStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.escalation import escalate, resolve_open_todos, resolve_todo


def open_todos(store):
    return store.list(HumanTask, status=HumanTaskStatus.open)


# -- filing -------------------------------------------------------------------


def test_dedup_key_beats_wording():
    """The same condition filed twice with different words (system vs planner
    phrasing) yields one open todo."""
    store = MemoryStore()
    first = escalate(store, "Fix codex login on hive-vm", "a", dedup_key="access:codex:hive-vm")
    second = escalate(
        store, "Log in to ChatGPT subscription on hive-vm", "b", dedup_key="access:codex:hive-vm"
    )
    assert first is not None and second is None
    assert len(open_todos(store)) == 1


def test_title_fallback_still_dedups_keyless_sites():
    store = MemoryStore()
    assert escalate(store, "Same title", "x", project_id="p1") is not None
    assert escalate(store, "Same title", "x", project_id="p1") is None
    # A different scope is a different condition.
    assert escalate(store, "Same title", "x", project_id="p2") is not None


def test_closed_todo_does_not_block_refiling():
    """A recurring condition gets one todo per episode, not one forever."""
    store = MemoryStore()
    todo = escalate(store, "Fix it", "x", dedup_key="k")
    todo.status = HumanTaskStatus.done
    store.put(todo)
    assert escalate(store, "Fix it", "x", dedup_key="k") is not None


def test_resolve_todo_closes_by_key_with_reason():
    store = MemoryStore()
    escalate(store, "Repair refresh", "x", dedup_key="repair:test-refresh:p")
    resolve_todo(store, "default", "repair:test-refresh:p", "a later refresh finalized")
    (todo,) = store.list(HumanTask)
    assert todo.status == HumanTaskStatus.done
    assert todo.resolved_reason == "a later refresh finalized"
    assert todo.done_at > 0


# -- resolution sweep ----------------------------------------------------------


def sweep_resolves(store, resolution, **kwargs) -> HumanTask:
    """File a todo with the given predicate, sweep, and return its final row."""
    escalate(store, "t", "i", dedup_key="k", resolution=resolution, **kwargs)
    resolve_open_todos(store)
    (todo,) = store.list(HumanTask)
    return todo


def test_no_predicate_means_manual_only():
    store = MemoryStore()
    todo = sweep_resolves(store, {}, kind=HumanTaskKind.external)
    assert todo.status == HumanTaskStatus.open


def test_unknown_check_is_ignored_not_fatal():
    store = MemoryStore()
    todo = sweep_resolves(store, {"check": "from-the-future"})
    assert todo.status == HumanTaskStatus.open


def test_task_inactive_predicate():
    store = MemoryStore()
    task = store.put(Task(project_id="p", workstream_id="w", repo="r", instructions="x"))
    escalate(
        store, "Cancel stuck task", "x", dedup_key="k",
        resolution={"check": "task_inactive", "task_id": task.id},
    )
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1  # still pending: condition holds

    task.status = TaskStatus.cancelled
    store.put(task)
    resolve_open_todos(store)
    (todo,) = store.list(HumanTask)
    assert todo.status == HumanTaskStatus.done
    assert "cancelled" in todo.resolved_reason


def test_story_verdict_predicate_and_stale_subject():
    store = MemoryStore()
    story = store.put(
        Story(project_id="p", workstream_id="w", key="launch", status=StoryStatus.blocked)
    )
    escalate(
        store, "Unblock sweep", "x", dedup_key="env:story",
        resolution={"check": "story_verdict", "story_id": story.id},
    )
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1

    story.status = StoryStatus.passing
    store.put(story)
    resolve_open_todos(store)
    assert open_todos(store) == []

    # A todo whose subject vanished resolves as stale instead of living forever.
    escalate(
        store, "Unblock other sweep", "x", dedup_key="env:gone",
        resolution={"check": "story_verdict", "story_id": "nonexistent"},
    )
    resolve_open_todos(store)
    assert open_todos(store) == []


def test_finding_decided_predicate():
    store = MemoryStore()
    finding = store.put(
        Finding(
            project_id="p", workstream_id="w", episode_id="e", story_key="s",
            summary="bug", status=FindingStatus.blocked,
        )
    )
    escalate(
        store, "Unblock confirmation", "x", dedup_key="k",
        resolution={"check": "finding_decided", "finding_id": finding.id},
    )
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1

    finding.status = FindingStatus.rejected
    store.put(finding)
    resolve_open_todos(store)
    assert open_todos(store) == []


def test_workstream_done_predicate():
    store = MemoryStore()
    ws = store.put(IssueItem(project_id="p", title="issue #7", status=IssueItemStatus.rejected))
    escalate(
        store, "Land issue #7 failed", "x", dedup_key="k",
        resolution={"check": "workstream_done", "workstream_id": ws.id},
    )
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1

    ws.status = IssueItemStatus.done
    store.put(ws)
    resolve_open_todos(store)
    assert open_todos(store) == []


def test_conversation_done_predicate():
    """A finalize-failure todo waits for the intake conversation to actually
    reach `done` — a merely-reopened conversation keeps it open."""
    store = MemoryStore()
    conv = store.put(
        AgentConversation(
            project_id="p", repo="r", backend="codex", status=ConversationStatus.open
        )
    )
    escalate(
        store, "Intake finalize did not land", "x", dedup_key="finalize",
        resolution={"check": "conversation_done", "conversation_id": conv.id},
    )
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1

    conv.status = ConversationStatus.done
    store.put(conv)
    resolve_open_todos(store)
    assert open_todos(store) == []


def test_orchestrator_ran_predicate():
    store = MemoryStore()
    escalate(
        store, "Fix Hive orchestrator", "x", dedup_key="k",
        resolution={"check": "orchestrator_ran", "project_id": "p"},
    )
    # A run recorded *before* the todo proves nothing.
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1

    store.put(OrchestratorRun(project_id="p", created_at=time.time() + 1))
    resolve_open_todos(store)
    (todo,) = store.list(HumanTask)
    assert todo.status == HumanTaskStatus.done


def test_project_state_not_predicate():
    store = MemoryStore()
    project = store.put(Project(name="td", state=ProjectState.blocked_resources))
    escalate(
        store, "Enable testing capabilities", "x", dedup_key="k",
        resolution={
            "check": "project_state_not",
            "project_id": project.id,
            "state": str(ProjectState.blocked_resources),
        },
    )
    resolve_open_todos(store)
    assert len(open_todos(store)) == 1

    project.state = ProjectState.working
    store.put(project)
    resolve_open_todos(store)
    assert open_todos(store) == []
