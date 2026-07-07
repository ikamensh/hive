"""Machine/agent grouping shared by the dashboard and the resources page.

The grouping lives in one place now; both serializers (`machine_cards` for the
detailed page, `capacity_summary` for the dashboard) read from the same
`group_machines` output, so they can never disagree about what is online or
ready. These tests pin the grouping invariants and that agreement.
"""

import time

from hive._control.capacity import (
    agent_status,
    capacity_summary,
    group_machines,
    machine_cards,
    resource_available,
    subscription_candidates,
)
from hive.models import Machine, Resource, ResourceUsability, Runner, Subscription
from hive.agents import backend_licensing


def _usable(runner_id: str, backend: str = "claude", **kw) -> Resource:
    return Resource(runner_id=runner_id, backend=backend, usability_status=ResourceUsability.usable, **kw)


def test_every_resource_lands_in_exactly_one_card():
    machine = Machine(name="m1")
    on_machine = Runner(machine_id=machine.id, name="r1", backends=["claude"])
    unlinked = Runner(name="r2", backends=["codex"])
    resources = [
        _usable(on_machine.id, "claude"),
        _usable(unlinked.id, "codex"),
        _usable("ghost-runner", "cursor"),  # orphan: no runner record
    ]
    groups = group_machines([machine], [on_machine, unlinked], resources)

    grouped_ids = [res.id for g in groups for res in g.resources]
    assert sorted(grouped_ids) == sorted(r.id for r in resources)  # nothing lost or duplicated

    names = {g.machine.name for g in groups}
    assert machine.name in names  # the real machine
    assert "r2" in names  # unlinked runner gets a diagnostic card
    assert "unassigned" in names  # orphan resource gets a card
    assert next(g.machine for g in groups if g.machine.name == "r2").kind == "unlinked"


def test_serializers_agree_on_readiness():
    """machine_cards and capacity_summary derive from the same groups, so the
    `available` they report for a resource is identical."""
    machine = Machine(name="m1")
    runner = Runner(machine_id=machine.id, name="r1", backends=["claude", "codex"])
    ready = _usable(runner.id, "claude")
    cooling = _usable(runner.id, "codex", cooldown_until=time.time() + 600)
    groups = group_machines([machine], [runner], [ready, cooling])

    cards = machine_cards(groups)
    summary = capacity_summary(groups)

    card_avail = {res["id"]: res["available"] for c in cards for res in c["resources"]}
    agent_avail = {a["id"]: a["available"] for c in summary["machines"] for a in c["agents"]}
    assert card_avail == agent_avail
    assert card_avail[ready.id] is True and card_avail[cooling.id] is False

    assert summary["machines_total"] == len(cards)
    assert summary["agents_total"] == 2
    assert summary["agents_ready"] == 1
    assert cards[0]["online"] is True  # runner.last_seen defaults to now


def test_resource_available_requires_online_runner_advertising_backend():
    online = Runner(name="now", backends=["claude"])
    offline = Runner(name="old", backends=["claude"], last_seen=time.time() - 10_000)

    assert resource_available(_usable(online.id, "claude"), online) is True
    assert resource_available(_usable(online.id, "claude"), offline) is False
    assert resource_available(_usable(online.id, "claude"), None) is False
    # Runner is online but does not advertise this backend.
    assert resource_available(_usable(online.id, "gemini-cli"), online) is False


def test_agent_status_degrades_sensibly():
    online = Runner(name="now", backends=["claude"])
    offline = Runner(name="old", backends=["claude"], last_seen=time.time() - 10_000)

    assert agent_status(_usable(online.id), online) == "ready"
    assert agent_status(_usable(online.id), offline) == "offline"
    assert agent_status(_usable(online.id), None) == "offline"
    assert agent_status(_usable(online.id, cooldown_until=time.time() + 600), online) == "cooldown"
    assert agent_status(Resource(runner_id=online.id, backend="claude", enabled=False), online) == "disabled"
    assert agent_status(Resource(runner_id=online.id, backend="claude"), online) == "probe"


def test_discovery_offers_only_proven_unsubscribed_providers():
    """A usable agent with no Subscription is evidence to confirm; an already
    recorded provider or an unproven CLI is not offered. Evidence names the
    machine (the user-facing identity) when the resource is linked to one,
    falling back to the runner name for legacy rows."""
    runner = Runner(name="laptop", backends=["claude", "cursor", "codex"])
    resources = [
        _usable(runner.id, "claude"),  # proven, no subscription -> candidate
        _usable(runner.id, "cursor"),  # proven, already subscribed -> skip
        Resource(runner_id=runner.id, backend="codex"),  # discovered but unproven -> skip
    ]
    subs = [Subscription(provider="cursor")]

    candidates = subscription_candidates(subs, resources, [runner])

    assert [c["provider"] for c in candidates] == ["claude"]
    assert candidates[0]["evidence"] == "usable on laptop"  # runner-name fallback

    machine = Machine(name="macbook")
    linked = _usable(runner.id, "claude", machine_id=machine.id)
    [candidate] = subscription_candidates(subs, [linked], [runner], [machine])
    assert candidate["evidence"] == "usable on macbook"  # machine link wins


def test_discovery_carries_provider_rulebook_licensing():
    """Each candidate starts from the backend's licensing default so confirming
    one does not silently guess portable/machine-bound wrong."""
    runner = Runner(name="vm", backends=["cursor", "claude"])
    resources = [_usable(runner.id, "cursor"), _usable(runner.id, "claude")]

    by_provider = {c["provider"]: c for c in subscription_candidates([], resources, [runner])}

    assert by_provider["cursor"]["licensing_mode"] == backend_licensing("cursor") == "portable"
    assert by_provider["claude"]["licensing_mode"] == backend_licensing("claude") == "machine_bound"


def test_discovery_dedupes_a_provider_usable_on_many_machines():
    """The same backend proven on two machines is one subscription to confirm."""
    a = Runner(name="a", backends=["claude"])
    b = Runner(name="b", backends=["claude"])
    resources = [_usable(a.id, "claude"), _usable(b.id, "claude")]

    candidates = subscription_candidates([], resources, [a, b])

    assert [c["provider"] for c in candidates] == ["claude"]
