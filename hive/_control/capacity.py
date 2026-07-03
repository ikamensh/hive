"""Grouping of agent capacity under durable machines.

One implementation, two consumers: the home dashboard wants a lightweight
readiness summary (`capacity_summary`); the resources page wants the same
grouping with full per-resource detail (`machine_cards`). The web client used
to re-derive this grouping itself — it now just renders what the server groups.
"""

from __future__ import annotations

from dataclasses import dataclass

from hive.models import Machine, Resource, ResourceUsability, Runner, Subscription, now
from hive.runner._backends import backend_licensing


@dataclass
class MachineGroup:
    """A machine card and the runners and agents attached to it.

    Legacy runner-only records are synthesized into diagnostic machine cards so
    capacity never disappears, but they are not a normal user-facing category.
    """

    machine: Machine
    runners: list[Runner]
    resources: list[Resource]

    @property
    def online(self) -> bool:
        return any(r.online() for r in self.runners)

    @property
    def last_seen(self) -> float:
        return max([self.machine.last_seen, *[r.last_seen for r in self.runners]], default=0.0)

    def runner_for(self, resource: Resource) -> Runner | None:
        return next((r for r in self.runners if r.id == resource.runner_id), None)


def resource_available(resource: Resource, runner: Runner | None) -> bool:
    """Dispatchable right now: usable, off cooldown, on an online runner that
    actually advertises the backend. Matches the per-resource `available` flag."""
    return (
        resource.available()
        and runner is not None
        and runner.online()
        and resource.backend in runner.backends
    )


def agent_status(resource: Resource, runner: Runner | None) -> str:
    """One word for how dispatchable this (runner, backend) unit is right now."""
    if not resource.enabled:
        return "disabled"
    if runner is None or not runner.online():
        return "offline"
    if resource.cooldown_until > now():
        return "cooldown"
    if resource.usability_status == ResourceUsability.usable:
        return "ready"
    if resource.usability_status == ResourceUsability.probing:
        return "probing"
    if resource.usability_status == ResourceUsability.failed:
        return "failed"
    return "probe"  # discovered but never proven


def _unlinked_machine(runner: Runner) -> Machine:
    """A runner with no recognized machine record still deserves a card."""
    return Machine(
        id=f"runner:{runner.id}",
        workspace_id=runner.workspace_id,
        name=runner.name,
        hostname=runner.name,
        kind="unlinked",
        device_kind="unknown",
        first_seen=runner.last_seen,
        last_seen=runner.last_seen,
    )


def _unassigned_machine() -> Machine:
    return Machine(
        id="unassigned-resources",
        workspace_id="",
        name="unassigned",
        device_kind="unknown",
        first_seen=0.0,
        last_seen=0.0,
    )


def group_machines(
    machines: list[Machine], runners: list[Runner], resources: list[Resource]
) -> list[MachineGroup]:
    """Bucket agents under their machine; give unlinked runners and orphan
    resources their own cards so nothing disappears."""
    machine_ids = {m.id for m in machines}
    claimed: set[str] = set()
    groups: list[MachineGroup] = []

    for machine in machines:
        machine_runners = [r for r in runners if r.machine_id == machine.id]
        runner_ids = {r.id for r in machine_runners}
        machine_resources = [
            res for res in resources if res.machine_id == machine.id or res.runner_id in runner_ids
        ]
        claimed.update(res.id for res in machine_resources)
        groups.append(MachineGroup(machine, machine_runners, machine_resources))

    for runner in runners:
        if runner.machine_id and runner.machine_id in machine_ids:
            continue
        runner_resources = [res for res in resources if res.runner_id == runner.id]
        claimed.update(res.id for res in runner_resources)
        groups.append(MachineGroup(_unlinked_machine(runner), [runner], runner_resources))

    orphans = [res for res in resources if res.id not in claimed]
    if orphans:
        groups.append(MachineGroup(_unassigned_machine(), [], orphans))

    return groups


def machine_cards(groups: list[MachineGroup]) -> list[dict]:
    """Full detail per card — for the resources management page."""
    return [
        {
            "machine": g.machine.model_dump(),
            "online": g.online,
            "last_seen": g.last_seen,
            "runners": [{**r.model_dump(), "online": r.online()} for r in g.runners],
            "resources": [
                {**res.model_dump(), "available": resource_available(res, g.runner_for(res))}
                for res in g.resources
            ],
        }
        for g in groups
    ]


def subscription_candidates(
    subscriptions: list[Subscription],
    resources: list[Resource],
    runners: list[Runner],
    machines: list[Machine] = (),  # type: ignore[assignment]
) -> list[dict]:
    """Providers proven usable on a machine but not yet recorded as a Subscription.

    A usable agent is direct evidence the user holds access to that provider, so
    Hive offers it back as a one-click confirmation instead of making the user
    re-type what discovery already found. Only proven-usable resources count —
    an installed-but-unprobed CLI is not yet evidence of a real subscription.
    Candidates carry the provider-rulebook licensing default so confirming one
    starts from the right portable/machine-bound guess.
    """
    have = {s.provider for s in subscriptions}
    machine_name = {m.id: m.name for m in machines}
    runner_name = {r.id: r.name for r in runners}
    candidates: dict[str, dict] = {}
    for res in resources:
        if res.backend in have or res.backend in candidates:
            continue
        if res.usability_status != ResourceUsability.usable:
            continue
        # Evidence names the machine (the user-facing identity); a legacy
        # resource with no machine link falls back to its runner.
        where = machine_name.get(res.machine_id) or runner_name.get(res.runner_id, res.runner_id)
        candidates[res.backend] = {
            "provider": res.backend,
            "licensing_mode": backend_licensing(res.backend),
            "evidence": f"usable on {where}" if where else "usable",
        }
    return list(candidates.values())


def capacity_summary(groups: list[MachineGroup]) -> dict:
    """Lightweight readiness rollup — for the home dashboard."""
    cards = []
    ready_by_id: dict[str, bool] = {}
    for g in groups:
        agents = []
        for res in g.resources:
            available = resource_available(res, g.runner_for(res))
            ready_by_id[res.id] = available
            agents.append(
                {
                    "id": res.id,
                    "backend": res.backend,
                    "status": agent_status(res, g.runner_for(res)),
                    "available": available,
                    "cooldown_until": res.cooldown_until,
                    "runner_id": res.runner_id,
                }
            )
        cards.append(
            {
                "id": g.machine.id,
                "name": g.machine.name,
                "hostname": g.machine.hostname,
                "kind": g.machine.kind,
                "device_kind": g.machine.device_kind,
                "online": g.online,
                "last_seen": g.last_seen,
                "agents": agents,
            }
        )
    return {
        "machines": cards,
        "machines_total": len(cards),
        "machines_online": sum(1 for c in cards if c["online"]),
        "agents_total": len(ready_by_id),
        "agents_ready": sum(1 for ready in ready_by_id.values() if ready),
    }
