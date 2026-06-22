"""Single-pass home-dashboard overview.

The web home page used to assemble its state by fetching the project list and
then one heavy detail payload per project (an N+1 waterfall). This builds the
same picture server-side in one read so the client makes a single request and
the counting lives in one place.

Read-only: it never creates default workstreams or mutates records, so it is
safe to poll frequently.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from hive.models import (
    HumanTask,
    HumanTaskStatus,
    Machine,
    Project,
    ProjectWorkstream,
    Question,
    QuestionStatus,
    Resource,
    ResourceUsability,
    Runner,
    Subscription,
    Task,
    TaskStatus,
    Workstream,
    WorkstreamSource,
    WorkstreamStatus,
    now,
)

# Issue-workstream states that need the human before Hive can proceed.
ISSUE_BLOCKED = (WorkstreamStatus.blocked_clarity, WorkstreamStatus.rejected)

# Live-task and attention lists are capped — the dashboard shows highlights and
# links into the project for the full set.
LIVE_TASK_CAP = 30
ATTENTION_CAP = 20


def _bucket(items, key) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for item in items:
        out[key(item)].append(item)
    return out


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


def _resource_available(resource: Resource, runner: Runner | None) -> bool:
    """Matches the /api/resources `available` field: usable, not cooling down,
    on an online runner that actually advertises the backend."""
    return (
        resource.available()
        and runner is not None
        and runner.online()
        and resource.backend in runner.backends
    )


def _agent_payload(resource: Resource, runner: Runner | None) -> dict:
    return {
        "id": resource.id,
        "backend": resource.backend,
        "status": agent_status(resource, runner),
        "available": _resource_available(resource, runner),
        "cooldown_until": resource.cooldown_until,
        "runner_id": resource.runner_id,
    }


def _machine_card(machine: Machine, runners: list[Runner], resources: list[Resource]) -> dict:
    runner_by_id = {r.id: r for r in runners}
    online = any(r.online() for r in runners)
    last_seen = max([machine.last_seen, *[r.last_seen for r in runners]], default=0.0)
    agents = [_agent_payload(res, runner_by_id.get(res.runner_id)) for res in resources]
    return {
        "id": machine.id,
        "name": machine.name,
        "hostname": machine.hostname,
        "kind": machine.kind,
        "device_kind": machine.device_kind,
        "online": online,
        "last_seen": last_seen,
        "agents": agents,
    }


def _virtual_machine(runner: Runner) -> Machine:
    """A runner with no recognized machine record still deserves a card."""
    return Machine(
        id=f"runner:{runner.id}",
        workspace_id=runner.workspace_id,
        name=runner.name,
        hostname=runner.name,
        kind="runner",
        device_kind="unknown",
        first_seen=runner.last_seen,
        last_seen=runner.last_seen,
    )


def build_capacity(
    machines: list[Machine], runners: list[Runner], resources: list[Resource]
) -> dict:
    """Group agents under the machine (or virtual runner-machine) that hosts
    them. Mirrors the web buildMachineCards grouping, server-owned now."""
    machine_ids = {m.id for m in machines}
    claimed: set[str] = set()
    cards: list[dict] = []

    for machine in machines:
        machine_runners = [r for r in runners if r.machine_id == machine.id]
        runner_ids = {r.id for r in machine_runners}
        machine_resources = [
            res for res in resources if res.machine_id == machine.id or res.runner_id in runner_ids
        ]
        claimed.update(res.id for res in machine_resources)
        cards.append(_machine_card(machine, machine_runners, machine_resources))

    for runner in runners:
        if runner.machine_id and runner.machine_id in machine_ids:
            continue
        runner_resources = [res for res in resources if res.runner_id == runner.id]
        claimed.update(res.id for res in runner_resources)
        cards.append(_machine_card(_virtual_machine(runner), [runner], runner_resources))

    orphans = [res for res in resources if res.id not in claimed]
    if orphans:
        unassigned = Machine(
            id="unassigned-resources",
            workspace_id="",
            name="unassigned",
            device_kind="unknown",
            first_seen=0.0,
            last_seen=0.0,
        )
        cards.append(_machine_card(unassigned, [], orphans))

    agents_total = len(resources)
    agents_ready = sum(1 for card in cards for a in card["agents"] if a["available"])
    return {
        "machines": cards,
        "machines_total": len(cards),
        "machines_online": sum(1 for card in cards if card["online"]),
        "agents_total": agents_total,
        "agents_ready": agents_ready,
    }


def build_overview(store, workspace_id: str, spend_today: Callable[[str], float]) -> dict:
    """Assemble everything the home dashboard needs in one pass.

    `spend_today(project_id)` is injected (the supervisor owns that sum) so this
    stays a pure read over the store and is trivial to test.
    """
    projects = [
        p for p in store.list(Project, workspace_id=workspace_id) if not p.archived
    ]
    name_by_id = {p.id: p.name for p in projects}

    items_by_project = _bucket(store.list(Workstream, workspace_id=workspace_id), lambda w: w.project_id)
    streams_by_project = _bucket(store.list(ProjectWorkstream, workspace_id=workspace_id), lambda s: s.project_id)
    tasks_by_project = _bucket(store.list(Task, workspace_id=workspace_id), lambda t: t.project_id)
    questions = store.list(Question, workspace_id=workspace_id)
    q_by_project = _bucket(questions, lambda q: q.project_id)

    project_rows = []
    spend_total = 0.0
    budget_total = 0.0
    for project in projects:
        items = items_by_project.get(project.id, [])
        tasks = tasks_by_project.get(project.id, [])
        spend = spend_today(project.id)
        spend_total += spend
        if project.daily_budget_usd > 0:
            budget_total += project.daily_budget_usd
        project_rows.append(
            {
                "id": project.id,
                "name": project.name,
                "spec_repo": project.spec_repo,
                "state": project.state,
                "paused": project.paused,
                "created_at": project.created_at,
                "daily_budget_usd": project.daily_budget_usd,
                "spend_today": spend,
                "counts": {
                    "active": sum(1 for w in items if w.status == WorkstreamStatus.active),
                    "running": sum(1 for t in tasks if t.status == TaskStatus.running),
                    "questions": sum(
                        1 for q in q_by_project.get(project.id, []) if q.status == QuestionStatus.open
                    ),
                    "blockers": sum(
                        1
                        for w in items
                        if w.source == WorkstreamSource.issue and w.status in ISSUE_BLOCKED
                    ),
                    "streams": len(streams_by_project.get(project.id, [])),
                },
            }
        )

    capacity = build_capacity(
        store.list(Machine, workspace_id=workspace_id),
        store.list(Runner, workspace_id=workspace_id),
        store.list(Resource, workspace_id=workspace_id),
    )

    running = [t for t in store.list(Task, workspace_id=workspace_id) if t.status == TaskStatus.running]
    running.sort(key=lambda t: t.started_at, reverse=True)
    live_tasks = [
        {
            "id": t.id,
            "project_id": t.project_id,
            "project_name": name_by_id.get(t.project_id, ""),
            "backend": t.backend,
            "model": t.model,
            "kind": t.kind,
            "started_at": t.started_at,
            "issue_number": t.issue_number,
        }
        for t in running[:LIVE_TASK_CAP]
    ]

    open_questions = [q for q in questions if q.status == QuestionStatus.open]
    open_questions.sort(key=lambda q: q.created_at, reverse=True)
    open_todos = [
        t
        for t in store.list(HumanTask, workspace_id=workspace_id)
        if t.status == HumanTaskStatus.open
    ]
    open_todos.sort(key=lambda t: t.created_at, reverse=True)
    attention = {
        "count": len(open_questions) + len(open_todos),
        "questions": [
            {
                "id": q.id,
                "project_id": q.project_id,
                "project_name": name_by_id.get(q.project_id, ""),
                "text": q.text,
                "created_at": q.created_at,
            }
            for q in open_questions[:ATTENTION_CAP]
        ],
        "human_todos": [
            {
                "id": t.id,
                "project_id": t.project_id,
                "project_name": name_by_id.get(t.project_id, "") if t.project_id else "",
                "title": t.title,
                "instructions": t.instructions,
                "created_at": t.created_at,
            }
            for t in open_todos[:ATTENTION_CAP]
        ],
    }

    subscriptions = [
        s.model_dump() for s in store.list(Subscription, workspace_id=workspace_id)
    ]

    return {
        "projects": project_rows,
        "capacity": capacity,
        "live_tasks": live_tasks,
        "attention": attention,
        "subscriptions": subscriptions,
        "totals": {
            "tasks_running": len(running),
            "agents_ready": capacity["agents_ready"],
            "agents_total": capacity["agents_total"],
            "machines_online": capacity["machines_online"],
            "machines_total": capacity["machines_total"],
            "needs_you": attention["count"],
            "spend_today": spend_total,
            "budget_today": budget_total,
        },
    }
