"""The AI second-opinion on the todo board is close-only and code-guarded:
whatever the model answers, it can merge duplicates and retire provably-stale
todos — never touch predicated ones, never invent targets, never act beyond a
close with an audit note.

Properties verified (transport is scripted; no LLM, per test conventions):
- a duplicate verdict closes the newer wording and records which todo stays;
- guards hold against a malicious/confused model: self-references, unknown
  ids, closing a self-closing todo in favor of a manual one, and stale
  verdicts on predicated todos are all ignored;
- unparseable model output changes nothing;
- an all-quiet board (nothing but predicated todos, fewer than two) never
  spends a model call;
- the supervisor gate re-triages only when the open board changed.
"""

import json

from hive.models import HumanTask, HumanTaskStatus
from hive.persistence.store import MemoryStore
from hive._control.escalation import escalate
from hive._control.supervisor import Supervisor
from hive._control.todo_triage import triage_open_todos


def scripted(decisions):
    calls = []

    def transport(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"decisions": decisions})

    transport.calls = calls
    return transport


def open_ids(store):
    return {t.id for t in store.list(HumanTask, status=HumanTaskStatus.open)}


def test_duplicate_closes_one_wording_and_names_the_keeper():
    store = MemoryStore()
    keeper = escalate(store, "Fix cursor subscription", "billing is dead")
    dup = escalate(store, "Register cursor plan on raven", "please register")

    applied = triage_open_todos(
        store,
        scripted(
            [
                {"todo_id": keeper.id, "verdict": "keep"},
                {"todo_id": dup.id, "verdict": "duplicate", "of": keeper.id},
            ]
        ),
    )

    assert [d.verdict for d in applied] == ["duplicate"]
    assert open_ids(store) == {keeper.id}
    closed = store.get(HumanTask, dup.id)
    assert keeper.id in closed.resolved_reason


def test_stale_closes_predicate_less_todo_with_cited_reason():
    store = MemoryStore()
    zombie = escalate(store, "Bring cursor runner online", "task X is stuck")
    escalate(store, "Add DNS record", "still needed")

    applied = triage_open_todos(
        store,
        scripted(
            [
                {"todo_id": zombie.id, "verdict": "stale", "reason": "task X is cancelled"},
                {"todo_id": "unknown-id", "verdict": "stale", "reason": "x"},
            ]
        ),
    )

    assert len(applied) == 1
    assert store.get(HumanTask, zombie.id).resolved_reason == "triage: task X is cancelled"
    assert len(open_ids(store)) == 1


def test_guards_hold_against_a_confused_model():
    """Predicated todos belong to the deterministic sweep; a self-closing todo
    is never traded for a manual duplicate; self-references are no-ops."""
    store = MemoryStore()
    predicated = escalate(
        store, "Fix codex login on vm", "x",
        dedup_key="access:codex:vm",
        resolution={"check": "resource_usable", "backend": "codex", "runner_name": "vm"},
    )
    manual = escalate(store, "Log into ChatGPT on vm", "x")

    triage_open_todos(
        store,
        scripted(
            [
                # Try to retire the predicated todo both ways: as stale, and as a
                # duplicate of the manual one.
                {"todo_id": predicated.id, "verdict": "stale", "reason": "looks fine"},
                {"todo_id": predicated.id, "verdict": "duplicate", "of": manual.id},
                {"todo_id": manual.id, "verdict": "duplicate", "of": manual.id},  # self
                {"todo_id": manual.id, "verdict": "stale", "reason": ""},  # no evidence
            ]
        ),
    )

    assert open_ids(store) == {predicated.id, manual.id}

    # The manual wording *may* fold into the self-closing todo.
    applied = triage_open_todos(
        store, scripted([{"todo_id": manual.id, "verdict": "duplicate", "of": predicated.id}])
    )
    assert len(applied) == 1
    assert open_ids(store) == {predicated.id}


def test_unparseable_output_changes_nothing():
    store = MemoryStore()
    escalate(store, "a", "x")
    escalate(store, "b", "x")
    assert triage_open_todos(store, lambda p: "I think you should...") == []
    assert len(open_ids(store)) == 2


def test_quiet_board_spends_no_model_call():
    store = MemoryStore()
    escalate(store, "Fix login", "x", resolution={"check": "resource_usable"})
    transport = scripted([])
    assert triage_open_todos(store, transport) == []
    assert transport.calls == []


def test_supervisor_gate_fires_only_on_board_change():
    store = MemoryStore()
    supervisor = Supervisor(store, lambda p, e: None, todo_triage=lambda: None)
    supervisor._last_todo_triage = 0.0

    assert not supervisor._todo_triage_due()  # empty board == initial board

    escalate(store, "a", "x")
    assert supervisor._todo_triage_due()

    supervisor._triaged_board = supervisor._open_todo_board()
    assert not supervisor._todo_triage_due()  # same board: no re-review

    escalate(store, "b", "x")
    assert supervisor._todo_triage_due()
