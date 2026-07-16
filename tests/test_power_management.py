"""On-demand machine power management: the chief switches purpose-built
capability machines (an android VM, ...) on when capability-blocked work is
pending and off after an idle window — a stopped instance bills storage only,
so sporadic capability work stops costing 24/7 compute.

Properties verified:
- capability-blocked pending work powers on a matching offline `on_demand`
  machine exactly once per boot grace, and holds the human escalation while
  it boots; machines that don't serve the blocked bundle are never woken;
- a wake whose grace elapsed with the instance already `running` stops
  holding the escalation (broken box ≠ booting box);
- the idle sweep powers off an online `on_demand` machine only after
  `idle_stop_minutes` without matching work — a running task or pending
  within-budget matching work keeps it awake and refreshes the clock;
- a chief-stopped (asleep) machine files no dark-machine todo;
- registration records substrate identity, seeds the power policy only on
  first sight, and marks the row awake again.
"""

import time

from hive.models import (
    HumanTask,
    HumanTaskStatus,
    IssueItem,
    Machine,
    Project,
    ProjectState,
    Resource,
    ResourceUsability,
    Runner,
    Task,
    TaskStatus,
)
from hive.persistence.store import MemoryStore
from hive._control.supervisor import Supervisor


class FakeSubstrate:
    def __init__(self, state: str = "stopped") -> None:
        self.on: list[tuple[str, str]] = []
        self.off: list[tuple[str, str]] = []
        self.instance_state = state

    def power_on(self, zone: str, instance_id: str) -> None:
        self.on.append((zone, instance_id))

    def power_off(self, zone: str, instance_id: str) -> None:
        self.off.append((zone, instance_id))

    def state(self, zone: str, instance_id: str) -> str:
        return self.instance_state


def make_supervisor(store, substrate=None) -> Supervisor:
    return Supervisor(store, orchestrate=lambda pid, events: None, substrate=substrate)


def put_droid_machine(store, *, asleep=False, online=False, capabilities=("android", "docker")):
    """An on_demand machine offering gemini-cli with the given capabilities."""
    last_seen = time.time() - (0 if online else 7200)
    machine = store.put(
        Machine(
            name="droid",
            device_kind="server",
            substrate_provider="scaleway",
            substrate_instance_id="i-droid",
            substrate_zone="fr-par-1",
            power_policy="on_demand",
            asleep=asleep,
            last_seen=last_seen,
        )
    )
    runner = store.put(
        Runner(
            name="droid-r",
            machine_id=machine.id,
            backends=["gemini-cli"],
            capabilities=list(capabilities),
            last_seen=last_seen,
        )
    )
    return machine, runner


def put_android_project_with_pending_task(store):
    project = store.put(
        Project(name="p", spec_repo="x", required_capabilities=["android"])
    )
    ws = store.put(IssueItem(project_id=project.id, title="w"))
    store.put(
        Task(project_id=project.id, workstream_id=ws.id, repo="r",
             instructions="i", backend="gemini-cli")
    )
    return project


def open_todos(store):
    return [t for t in store.list(HumanTask) if t.status == HumanTaskStatus.open]


def test_blocked_work_wakes_matching_machine_once_and_holds_escalation():
    store = MemoryStore()
    project = put_android_project_with_pending_task(store)
    put_droid_machine(store, asleep=True)
    # A second offline on_demand machine without android must never be woken.
    other = store.put(
        Machine(name="plain", substrate_provider="scaleway",
                substrate_instance_id="i-plain", substrate_zone="fr-par-1",
                power_policy="on_demand", last_seen=time.time() - 7200)
    )
    store.put(Runner(name="plain-r", machine_id=other.id, backends=["gemini-cli"],
                     capabilities=["docker"], last_seen=time.time() - 7200))
    fake = FakeSubstrate()
    sup = make_supervisor(store, substrate=fake)

    assert sup.refresh_state(project) == ProjectState.blocked_resources
    assert fake.on == [("fr-par-1", "i-droid")]
    assert open_todos(store) == []  # escalation held while the machine boots

    sup.refresh_state(project)  # a later pass inside the boot grace
    assert fake.on == [("fr-par-1", "i-droid")]  # no second poweron


def test_wake_that_never_registers_stops_holding_escalation():
    store = MemoryStore()
    project = put_android_project_with_pending_task(store)
    machine, _ = put_droid_machine(store, asleep=True)
    fake = FakeSubstrate(state="running")  # powered on, runner never came up
    sup = make_supervisor(store, substrate=fake)
    machine.wake_requested_at = time.time() - sup.WAKE_GRACE_S - 1
    store.put(machine)

    sup.refresh_state(project)
    assert fake.on == []  # already running: no pointless poweron
    assert len(open_todos(store)) == 1  # the human hears about it


def test_idle_sweep_powers_off_only_after_quiet_window():
    store = MemoryStore()
    fake = FakeSubstrate()
    sup = make_supervisor(store, substrate=fake)
    machine, runner = put_droid_machine(store, online=True)

    machine.last_needed_at = time.time() - machine.idle_stop_minutes * 60 + 30
    store.put(machine)
    sup.power_down_idle_machines()
    assert fake.off == []  # still inside the idle window

    machine.last_needed_at = time.time() - machine.idle_stop_minutes * 60 - 1
    store.put(machine)
    sup.power_down_idle_machines()
    assert fake.off == [("fr-par-1", "i-droid")]
    assert store.get(Machine, machine.id).asleep


def test_matching_or_running_work_keeps_machine_awake():
    store = MemoryStore()
    fake = FakeSubstrate()
    sup = make_supervisor(store, substrate=fake)
    machine, runner = put_droid_machine(store, online=True)
    machine.last_needed_at = time.time() - machine.idle_stop_minutes * 60 - 1
    store.put(machine)

    # Pending within-budget work the machine could serve refreshes the clock.
    project = put_android_project_with_pending_task(store)
    sup.power_down_idle_machines()
    assert fake.off == []
    assert store.get(Machine, machine.id).last_needed_at > time.time() - 5

    # A running task on its runner pins it awake regardless of the clock.
    for task in store.list(Task, project_id=project.id):
        task.status = TaskStatus.running
        task.runner_id = runner.id
        store.put(task)
    machine.last_needed_at = time.time() - machine.idle_stop_minutes * 60 - 1
    store.put(machine)
    sup.power_down_idle_machines()
    assert fake.off == []


def test_work_anything_can_run_does_not_keep_machine_awake():
    """A pending task with no capability needs is servable by ordinary online
    machines — it must not pin the expensive on_demand box. Only work that
    nothing else can serve counts as demand for it."""
    store = MemoryStore()
    fake = FakeSubstrate()
    sup = make_supervisor(store, substrate=fake)
    machine, _ = put_droid_machine(store, online=True)
    machine.last_needed_at = time.time() - machine.idle_stop_minutes * 60 - 1
    store.put(machine)
    # An ordinary always-on machine also offers gemini-cli.
    plain = store.put(Machine(name="plain", device_kind="server"))
    store.put(Runner(name="plain-r", machine_id=plain.id, backends=["gemini-cli"]))
    # Capability-less pending work: anyone can run it.
    project = store.put(Project(name="p", spec_repo="x"))
    ws = store.put(IssueItem(project_id=project.id, title="w"))
    store.put(Task(project_id=project.id, workstream_id=ws.id, repo="r",
                   instructions="i", backend="gemini-cli"))

    sup.power_down_idle_machines()
    assert fake.off == [("fr-par-1", "i-droid")]


def test_asleep_machine_files_no_dark_todo():
    store = MemoryStore()
    sup = make_supervisor(store, substrate=FakeSubstrate())
    machine, _ = put_droid_machine(store, asleep=True)
    machine.last_seen = time.time() - 30 * 24 * 3600
    store.put(machine)

    sup.check_dark_machines()
    assert open_todos(store) == []
