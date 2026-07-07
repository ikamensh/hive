"""Subsystem introspection behind `hive show` / GET /api/show.

The full autonomous loop is hard to trust as a black box, so each subsystem
gets its own directly inspectable view:

- machines: every durable machine Hive recognizes, where it lives (hostname,
  OS), and whether a runner is heartbeating there right now.
- agents: every agent Hive could launch — (backend, machine) pairs with their
  dispatchability — plus the licenses (subscriptions) backing that capacity.
- limits: what each license knows about its own usage windows (provider
  gauges, reset times, empirical token estimates) — see `_control/limits.py`.
- autonomy: the recurring jobs the supervisor fires, at what period, and what
  each would do — on which agents/machines — if it resolved right now.

Read-only over the store (safe to poll); `spend_today` is injected like
`build_overview` because the supervisor owns that sum.
"""

from __future__ import annotations

import time
from typing import Callable

from hive._control.capacity import (
    MachineGroup,
    agent_status,
    group_machines,
    resource_available,
    subscription_candidates,
)
from hive._control.limits import limits_view
from hive._control.supervisor import Supervisor
from hive.fleet import DEFAULT_LIVENESS, Liveness
from hive._workstreams.issues import RESOLVE_BACKEND
from hive._workstreams.testing import auto_testing_decision
from hive.config.settings import Config
from hive.models import (
    Machine,
    Project,
    ProjectWorkstream,
    ProjectWorkstreamKind,
    Resource,
    ResourceUsability,
    Runner,
    Subscription,
)
from hive.runner._backends import BACKEND_NAMES, backend_licensing


_ERRORISH = ("error", "fail", "not logged", "denied", "unauthorized", "authentication", "quota")


def _first_line(text: str) -> str:
    """The most actionable line of a probe/exhaustion message: agent CLIs often
    front-load banner noise, so prefer the first error-looking line and fall
    back to the first non-empty one."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if any(marker in line.lower() for marker in _ERRORISH):
            return line
    return lines[0] if lines else ""


def machines_view(groups: list[MachineGroup], chief_machine_name: str = "") -> list[dict]:
    """Which machines are discoverable and where they live.

    `chief_machine_name` is the chief process's own machine (from its config);
    runner registration owns the Machine rows and stamps them `kind=runner`,
    so without this flag the view could not tell you where the chief lives."""
    now = time.time()
    rows = []
    for g in groups:
        m = g.machine
        verdict = DEFAULT_LIVENESS.assess(g.last_seen, m.device_kind, now=now)
        rows.append(
            {
                "id": m.id,
                "name": m.name,
                "hostname": m.hostname,
                "hosts_chief": bool(chief_machine_name)
                and chief_machine_name in (m.name, m.hostname),
                "machine_type": m.machine_type,
                "os": m.os,
                "arch": m.arch,
                "device_kind": m.device_kind,
                "kind": m.kind,
                "online": g.online,
                "last_seen": g.last_seen,
                "dark": verdict is Liveness.dark,
                "retired": verdict is Liveness.retired,
                "runners": [
                    {
                        "name": r.name,
                        "online": r.online(),
                        "last_seen": r.last_seen,
                        "backends": r.backends,
                        "capabilities": r.capabilities,
                    }
                    for r in g.runners
                ],
            }
        )
    return rows


def agents_view(groups: list[MachineGroup]) -> dict:
    """All agents we can launch and on what machines they are."""
    now = time.time()
    agents = []
    for g in groups:
        for res in g.resources:
            runner = g.runner_for(res)
            available = resource_available(res, runner)
            status = agent_status(res, runner)
            # The one line that tells the operator what to do about a
            # non-ready agent: the probe failure (usually a login ask) or the
            # quota message that started an active cooldown.
            note = ""
            if status == "failed":
                note = _first_line(res.last_probe_text)
            elif status == "cooldown":
                note = _first_line(res.last_exhaustion_text)
            elif status == "disabled":
                note = res.disabled_reason
            agents.append(
                {
                    "backend": res.backend,
                    "machine": g.machine.name,
                    "machine_id": g.machine.id,
                    "machine_online": g.online,
                    "status": status,
                    "available": available,
                    "note": note,
                    "licensing": backend_licensing(res.backend),
                    "cli_version": res.cli_version,
                    # An expired cooldown is history, not state — report 0.
                    "cooldown_until": res.cooldown_until if res.cooldown_until > now else 0.0,
                    "resource_id": res.id,
                }
            )
    return {
        "agents": sorted(agents, key=lambda a: (not a["available"], a["machine"], a["backend"])),
        "launchable_now": sum(1 for a in agents if a["available"]),
    }


def subscriptions_view(groups: list[MachineGroup], subscriptions: list[Subscription]) -> dict:
    """Subscriptions as expectations, diffed against reality.

    A subscription says "I own access to this provider"; resources say where
    that access actually works. The diff is the operator's worklist: machines
    where the CLI exists but the login is missing (`login_needed`), proven
    capacity nobody recorded (`unregistered`), and hive-supported backends the
    org holds no access to at all (`unowned`)."""
    rows = []
    for s in subscriptions:
        mode = s.licensing_mode
        if mode == "unknown":
            mode = backend_licensing(s.provider)  # provider-rulebook default
        serving: dict[str, None] = {}
        login_needed: dict[str, str] = {}
        for g in groups:
            for res in g.resources:
                if res.backend != s.provider:
                    continue
                if res.usability_status == ResourceUsability.usable and res.enabled:
                    serving[g.machine.name] = None
                    login_needed.pop(g.machine.name, None)
                elif res.enabled and g.machine.name not in serving:
                    note = _first_line(res.last_probe_text) or "never probed"
                    login_needed[g.machine.name] = note
        rows.append(
            {
                "provider": s.provider,
                "plan": s.plan,
                "licensing_mode": mode,
                "notes": s.notes,
                "serving": list(serving),
                "login_needed": [
                    {"machine": name, "note": note} for name, note in login_needed.items()
                ],
            }
        )

    all_runners = [r for g in groups for r in g.runners]
    all_resources = [res for g in groups for res in g.resources]
    have = {s.provider for s in subscriptions}
    usable_anywhere = {
        res.backend
        for res in all_resources
        if res.usability_status == ResourceUsability.usable
    }
    return {
        "subscriptions": rows,
        "unregistered": subscription_candidates(
            subscriptions, all_resources, all_runners, [g.machine for g in groups]
        ),
        "unowned": sorted(set(BACKEND_NAMES) - have - usable_anywhere),
    }


def _launchable_machines(groups: list[MachineGroup], backends: list[str]) -> list[str]:
    """Machine names where any of `backends` is dispatchable right now."""
    names = []
    for g in groups:
        if any(
            res.backend in backends and resource_available(res, g.runner_for(res))
            for res in g.resources
        ):
            names.append(g.machine.name)
    return names


def autonomy_view(
    store,
    workspace_id: str,
    groups: list[MachineGroup],
    spend_today: Callable[[str], float],
    config: Config,
) -> list[dict]:
    """The recurring jobs the supervisor would fire: period, gates, and what
    each would do — using which agents/machines — if it resolved now."""
    now = time.time()
    jobs: list[dict] = []

    projects = [
        p for p in store.list(Project, workspace_id=workspace_id) if not p.archived
    ]
    streams = store.list(ProjectWorkstream, workspace_id=workspace_id)
    issue_backend = config.issue_backend or RESOLVE_BACKEND
    testing_backends = list(
        dict.fromkeys(
            [config.test_refresh_backend, config.test_sweep_backend, config.test_confirm_backend]
        )
    )

    for project in projects:
        blocked = []
        if project.paused:
            blocked.append("project paused")
        if not project.spec_repo.strip():
            blocked.append("no spec repo configured")
        spend = spend_today(project.id)
        over_budget = 0 < project.daily_budget_usd <= spend
        repos = [r for r in dict.fromkeys([*project.member_repos, project.spec_repo]) if r.strip()]

        if project.ci_autofix:
            jobs.append(
                {
                    "job": "ci_check",
                    "project_id": project.id,
                    "project_name": project.name,
                    "repos": repos,
                    "interval_s": Supervisor.CI_CHECK_INTERVAL_S,
                    "action_now": ""
                    if blocked
                    else "poll each repo's default-branch CI; file + auto-fix an issue if red",
                    "reason": "; ".join(blocked),
                    "backends": [issue_backend],
                    "machines": _launchable_machines(groups, [issue_backend]),
                    "blocked_by": blocked,
                }
            )

        if project.testing_auto:
            testing_blocked = blocked + (["over today's budget"] if over_budget else [])
            existing = {
                s.repo: s
                for s in streams
                if s.project_id == project.id and s.kind == ProjectWorkstreamKind.testing
            }
            for repo in repos:
                # A repo without a testing workstream yet gets a transient one:
                # its empty backlog is exactly what the first check would see.
                workstream = existing.get(repo) or ProjectWorkstream(
                    workspace_id=workspace_id,
                    project_id=project.id,
                    kind=ProjectWorkstreamKind.testing,
                    title=f"Testing: {repo}",
                    repo=repo,
                )
                action, reason = auto_testing_decision(store, project, workstream, now_epoch=now)
                jobs.append(
                    {
                        "job": "testing_check",
                        "project_id": project.id,
                        "project_name": project.name,
                        "repos": [repo],
                        "workstream_id": workstream.id if repo in existing else "",
                        "interval_s": Supervisor.TESTING_CHECK_INTERVAL_S,
                        "action_now": "" if testing_blocked else action,
                        "reason": "; ".join(testing_blocked) or reason,
                        "backends": testing_backends,
                        "machines": _launchable_machines(groups, testing_backends),
                        "blocked_by": testing_blocked,
                    }
                )

    dark_now = [
        row["name"]
        for row in machines_view(groups)
        if row["dark"]
    ]
    jobs.append(
        {
            "job": "dark_machine_watch",
            "project_id": "",
            "project_name": "(org-wide)",
            "repos": [],
            "interval_s": Supervisor.TICK_S,
            "action_now": (
                f"file operator todos for dark machines: {', '.join(dark_now)}" if dark_now else ""
            ),
            "reason": "" if dark_now else "no machine past its dark threshold (laptop 24h, server 4h)",
            "backends": [],
            "machines": dark_now,
            "blocked_by": [],
        }
    )
    return jobs


def build_show(
    store, workspace_id: str, spend_today: Callable[[str], float], config: Config
) -> dict:
    """Assemble all subsystem views in one read."""
    groups = group_machines(
        store.list(Machine, workspace_id=workspace_id),
        store.list(Runner, workspace_id=workspace_id),
        store.list(Resource, workspace_id=workspace_id),
    )
    return {
        "machines": machines_view(groups, chief_machine_name=config.machine_name),
        "agents": agents_view(groups),
        "subscriptions": subscriptions_view(
            groups, store.list(Subscription, workspace_id=workspace_id)
        ),
        "limits": limits_view(store, workspace_id, groups),
        "autonomy": autonomy_view(store, workspace_id, groups, spend_today, config),
    }
