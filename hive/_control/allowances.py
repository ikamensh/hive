"""Per-project agent allowances: count-based session grants.

The money budget cannot see subscription usage (subscription CLIs report ~zero
cost), so `Project.agent_grants` caps projects in the unit subscriptions
actually meter: agent sessions per UTC day, optionally restricted to
(backend, model) pairs. Grants are additive permissions — restriction is
expressed by omission. Design: wiki/agent-allowances.md.

Pure functions over grants and today's tasks; no store writes. Accounting is a
stateless recompute (like `spend_today`): assign today's dispatched sessions
chronologically, each to the matching grant with the most headroom (unlimited
first), so limited "any" capacity is preserved for tasks nothing else covers.
"""

from __future__ import annotations

import datetime
from typing import Iterable

from hive.models import AgentGrant, Task, TaskKind

# Kinds that never consume (or need) a session grant: probes are org-level
# health checks; preflight is a runner self-check, not an agent session.
EXEMPT_KINDS = (TaskKind.probe, TaskKind.preflight)


def utc_day_start() -> float:
    """Epoch seconds for 00:00 UTC today — the daily-allowance window boundary
    (shared with the money budget)."""
    midnight = datetime.datetime.now(datetime.UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight.timestamp()


def exempt(task: Task) -> bool:
    return task.kind in EXEMPT_KINDS


def grant_matches(grant: AgentGrant, backend: str, model: str) -> bool:
    """Does this grant cover (backend, model)? An empty task model means
    "backend default" and only matches grants that don't restrict models: a
    model-listing grant names what it allows, and callers resolving through it
    set the model explicitly, so restricted tasks are always concrete."""
    if grant.backends and backend not in grant.backends:
        return False
    if grant.models and model not in grant.models:
        return False
    return True


def permitted(grants: list[AgentGrant], backend: str, model: str) -> bool:
    """Is (backend, model) allowed at all today (ignoring session counts)?
    No grants = no limits."""
    return not grants or any(grant_matches(g, backend, model) for g in grants)


def sessions_today(tasks: Iterable[Task], day_start: float) -> list[Task]:
    """Today's agent sessions, oldest first: every task that reached a runner
    today (running or finished — a consumed session counts even if it failed),
    grant-exempt kinds excluded. Pending tasks have started_at == 0."""
    return sorted(
        (t for t in tasks if not exempt(t) and t.started_at >= day_start),
        key=lambda t: t.started_at,
    )


def _pick(grants: list[AgentGrant], left: list[int | None], backend: str, model: str) -> int | None:
    """The grant a session is assigned to: unlimited first, then the matching
    grant with the most headroom. May return a grant that is already at (or
    below) zero — an over-consumed day after a mid-day grant edit stays
    visible as negative headroom instead of vanishing."""
    best: int | None = None
    for i, g in enumerate(grants):
        if not grant_matches(g, backend, model):
            continue
        if left[i] is None:
            return i
        if best is None or left[i] > left[best]:  # type: ignore[operator]
            best = i
    return best


def consume(grants: list[AgentGrant], left: list[int | None], backend: str, model: str) -> None:
    """Record one session against the grant `_pick` assigns it to, in place."""
    pick = _pick(grants, left, backend, model)
    if pick is not None and left[pick] is not None:
        left[pick] -= 1  # type: ignore[operator]


def remaining(grants: list[AgentGrant], sessions: Iterable[Task]) -> list[int | None]:
    """Per-grant headroom after today's sessions (None = unlimited)."""
    left: list[int | None] = [g.sessions_per_day for g in grants]
    for task in sessions:
        consume(grants, left, task.backend, task.model)
    return left


def admits(grants: list[AgentGrant], left: list[int | None], backend: str, model: str) -> bool:
    """May one more (backend, model) session start right now? No grants = yes."""
    if not grants:
        return True
    return any(
        grant_matches(g, backend, model) and (left[i] is None or left[i] > 0)
        for i, g in enumerate(grants)
    )


def resolve_agent(
    grants: list[AgentGrant], backend: str, model: str = ""
) -> tuple[str, str]:
    """Map a pipeline's preferred (backend, model) onto a permitted pair.

    The preference stands when permitted (or when there are no grants);
    otherwise the first grant supplies the pair — keeping the preferred
    backend/model wherever the grant doesn't restrict them, else taking the
    grant's first listed entry. Session counts are not consulted: an exhausted
    allowance makes the task wait at dispatch until the UTC-midnight reset,
    exactly like the money budget."""
    if permitted(grants, backend, model):
        return backend, model
    g = grants[0]
    resolved_backend = backend if (not g.backends or backend in g.backends) else g.backends[0]
    resolved_model = model if (not g.models or model in g.models) else g.models[0]
    return resolved_backend, resolved_model


def describe(grants: list[AgentGrant], left: list[int | None]) -> str:
    """One line for humans and the planner: what's allowed and what's left."""
    if not grants:
        return "no limits"
    parts = []
    for g, headroom in zip(grants, left):
        backends = ",".join(g.backends) or "any backend"
        models = ",".join(g.models) or "any model"
        quota = (
            "unlimited"
            if g.sessions_per_day is None
            else f"{max(headroom or 0, 0)}/{g.sessions_per_day} left today"
        )
        parts.append(f"{backends} × {models}: {quota}")
    return "; ".join(parts)


def allowance_view(grants: list[AgentGrant], tasks: Iterable[Task]) -> dict:
    """The read-side payload for the project view: grants with live headroom."""
    sessions = sessions_today(tasks, utc_day_start())
    left = remaining(grants, sessions)
    return {
        "limited": bool(grants),
        "sessions_today": len(sessions),
        "grants": [
            {**g.model_dump(), "remaining_today": left[i]} for i, g in enumerate(grants)
        ],
        "summary": describe(grants, left),
    }


def grant_problems(grants: list[AgentGrant], known_backends: Iterable[str]) -> str:
    """Validation for user-supplied grants: unknown backend names are almost
    certainly typos that would silently never match, so reject them up front.
    Model names are not validated — providers rename too often to allow-list."""
    known = set(known_backends)
    for g in grants:
        for backend in g.backends:
            if backend not in known:
                return f"unknown backend {backend!r} in agent_grants; use one of {sorted(known)}"
        if g.sessions_per_day is not None and g.sessions_per_day < 0:
            return "sessions_per_day must be >= 0 (omit it for unlimited)"
    return ""
