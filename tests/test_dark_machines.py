"""Dark-machine escalation: the chief is the only party that knows a runner's
machine went silent, so it files the operator todo — and withdraws it on
reconnect.

Regression origin: a laptop runner died (stale checkout crashed, launchd
couldn't respawn it) and stayed dark for 9 days with zero signal anywhere.

Properties verified:
- a recently-alive machine that crosses its dark threshold produces exactly
  one open todo, no matter how many supervisor steps observe the outage;
- the todo closes itself when the machine heartbeats again;
- a machine silent past the retirement window produces nothing (graveyard
  rows must not nag forever);
- a fresh offline episode after a recovery produces a fresh todo;
- laptops get a lenient threshold (they sleep), servers a strict one.
"""

import time

from hive.models import HumanTask, HumanTaskStatus, Machine
from hive.persistence.store import MemoryStore
from hive._control.supervisor import (
    MACHINE_DARK_AFTER_S,
    MACHINE_RETIRED_AFTER_S,
    Supervisor,
)

HOUR = 3600.0


def make_supervisor(store):
    return Supervisor(store, lambda p, e: None)


def put_machine(store, *, name="raven", device_kind="laptop", dark_for=0.0):
    return store.put(
        Machine(name=name, device_kind=device_kind, last_seen=time.time() - dark_for)
    )


def open_todos(store):
    return [t for t in store.list(HumanTask) if t.status == HumanTaskStatus.open]


def test_dark_machine_escalates_once():
    store = MemoryStore()
    supervisor = make_supervisor(store)
    put_machine(store, dark_for=MACHINE_DARK_AFTER_S["laptop"] + HOUR)

    supervisor.check_dark_machines()
    supervisor.check_dark_machines()  # outage persists across many steps

    todos = open_todos(store)
    assert len(todos) == 1
    assert "raven" in todos[0].title
    assert todos[0].project_id == ""  # org-wide, not tied to a project


def test_todo_closes_when_machine_returns():
    store = MemoryStore()
    supervisor = make_supervisor(store)
    machine = put_machine(store, dark_for=MACHINE_DARK_AFTER_S["laptop"] + HOUR)
    supervisor.check_dark_machines()
    assert len(open_todos(store)) == 1

    machine.last_seen = time.time()  # heartbeat: machine is back
    store.put(machine)
    supervisor.check_dark_machines()

    assert open_todos(store) == []
    done = store.list(HumanTask)[0]
    assert done.status == HumanTaskStatus.done and done.done_at > 0


def test_retired_machine_stays_silent():
    store = MemoryStore()
    supervisor = make_supervisor(store)
    put_machine(store, name="selftest-old", dark_for=MACHINE_RETIRED_AFTER_S + HOUR)

    supervisor.check_dark_machines()

    assert store.list(HumanTask) == []


def test_new_offline_episode_files_new_todo():
    store = MemoryStore()
    supervisor = make_supervisor(store)
    machine = put_machine(store, dark_for=MACHINE_DARK_AFTER_S["laptop"] + HOUR)
    supervisor.check_dark_machines()
    machine.last_seen = time.time()
    store.put(machine)
    supervisor.check_dark_machines()  # recovery closes the first todo

    machine.last_seen = time.time() - (MACHINE_DARK_AFTER_S["laptop"] + HOUR)
    store.put(machine)
    supervisor.check_dark_machines()  # second outage

    assert len(open_todos(store)) == 1
    assert len(store.list(HumanTask)) == 2


def test_thresholds_respect_availability_class():
    store = MemoryStore()
    supervisor = make_supervisor(store)
    # 6h dark: past the server threshold, within the laptop one.
    put_machine(store, name="sleepy-laptop", device_kind="laptop", dark_for=6 * HOUR)
    put_machine(store, name="quiet-server", device_kind="server", dark_for=6 * HOUR)

    supervisor.check_dark_machines()

    todos = open_todos(store)
    assert len(todos) == 1
    assert "quiet-server" in todos[0].title
