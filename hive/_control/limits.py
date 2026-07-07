"""License-limit accounting: turn runner-reported usage into dispatch facts.

The runner side (`hive/agents/usage.py`) collects two kinds of evidence and
ships them raw: provider usage snapshots (exact used-percent + reset time per
window) and reset hints parsed from rate-limit error messages. This module is
the chief-side consumer:

- `apply_snapshot` writes the freshest snapshot onto the `Resource` and keeps
  `cooldown_until` in sync with it — a window the provider says is spent cools
  the resource down until that window's own reset, and a healthy snapshot
  clears a cooldown that a snapshot created (self-healing after resets).
- `cooldown_after_exhaustion` picks the best cooldown when a task actually hit
  a limit: the error message's own reset time, else the binding window, else
  the soonest known window rollover, else a flat hour.
- `record_snapshot` / `record_exhaustion` append `LimitEvent` rows — the
  empirical history of what the windows looked like and where limits struck.
- `limits_view` is the operator report (`hive show limits`): provider-reported
  windows next to empirical token counts, plus a learned tokens-per-window
  budget wherever the history supports one.
"""

from __future__ import annotations

import time
from statistics import median

from pydantic import ValidationError

from hive.models import LimitEvent, Resource, Task, UsageWindow

# A window the provider reports ≥ this used-percent is treated as spent.
EXHAUSTED_PERCENT = 98.0
# Fallback cooldown when nothing tells us when the limit resets.
RATE_LIMIT_COOLDOWN_S = 3600.0
# A reset further out than a weekly window is a parse error, not a real reset.
RESET_HINT_MAX_S = 8 * 86400
# Marks cooldowns created from snapshots so fresh healthy snapshots may clear
# them (an error-based cooldown is only cleared by a succeeding task).
USAGE_WINDOW_PREFIX = "usage-window:"
# Snapshot events are only recorded on material movement, so the history holds
# usage trajectory, not heartbeat noise.
MATERIAL_PERCENT_DELTA = 1.0
_EVENT_SCAN_LIMIT = 300
_TASK_SCAN_LIMIT = 500


def parse_snapshot(raw: dict) -> tuple[str, str, float, list[UsageWindow]] | None:
    """(plan, source, captured_at, windows) from a runner-shipped snapshot
    dict, or None when it is empty or malformed (runners are trusted but a
    version-skewed one must not corrupt resources)."""
    if not raw:
        return None
    try:
        windows = [UsageWindow.model_validate(w) for w in raw.get("windows") or []]
        captured_at = float(raw.get("captured_at") or 0.0)
    except (ValidationError, TypeError, ValueError):
        return None
    if not windows or captured_at <= 0:
        return None
    return str(raw.get("plan") or ""), str(raw.get("source") or ""), captured_at, windows


def apply_snapshot(resource: Resource, raw: dict, *, now: float | None = None) -> bool:
    """Write a usage snapshot onto `resource` (latest wins) and sync the
    cooldown. Returns True when usage moved materially since the previous
    snapshot — the caller's cue to record a history event."""
    parsed = parse_snapshot(raw)
    if parsed is None:
        return False
    plan, source, captured_at, windows = parsed
    if captured_at <= resource.usage_captured_at:  # stale or duplicate delivery
        return False
    material = _material_change(resource.usage_windows, windows)
    resource.usage_plan = plan or resource.usage_plan
    resource.usage_source = source
    resource.usage_captured_at = captured_at
    resource.usage_windows = windows
    _sync_cooldown(resource, windows, now=now)
    return material


def _material_change(old: list[UsageWindow], new: list[UsageWindow]) -> bool:
    old_by_kind = {w.kind: w for w in old}
    if len(old) != len(new):
        return True
    for window in new:
        prev = old_by_kind.get(window.kind)
        if (
            prev is None
            or abs(prev.used_percent - window.used_percent) >= MATERIAL_PERCENT_DELTA
            or abs(prev.resets_at - window.resets_at) > 60
        ):
            return True
    return False


def _blocking(windows: list[UsageWindow], now: float) -> list[UsageWindow]:
    return [w for w in windows if w.used_percent >= EXHAUSTED_PERCENT and w.resets_at > now]


def _sync_cooldown(resource: Resource, windows: list[UsageWindow], *, now: float | None) -> None:
    now = time.time() if now is None else now
    blocking = _blocking(windows, now)
    if blocking:
        # Usable again only when every spent window has rolled over.
        worst = max(blocking, key=lambda w: w.resets_at)
        resource.mark_exhausted(
            until=worst.resets_at,
            at=now,
            text=f"{USAGE_WINDOW_PREFIX} {worst.kind} at {worst.used_percent:.0f}%",
            task_id=resource.last_exhaustion_task_id,
        )
    elif resource.cooldown_until > now and resource.last_exhaustion_text.startswith(
        USAGE_WINDOW_PREFIX
    ):
        resource.clear_exhaustion()


def cooldown_after_exhaustion(
    resource: Resource,
    *,
    reset_at_hint: float,
    snapshot: dict,
    now: float | None = None,
) -> float:
    """When a task hit a rate limit: the moment it makes sense to retry.

    Preference order: the reset time the error message itself named, the
    binding window from the freshest snapshot, the soonest upcoming window
    rollover (a natural retry point even when we don't know which window
    tripped), and only then the blind flat cooldown."""
    now = time.time() if now is None else now
    if now < reset_at_hint <= now + RESET_HINT_MAX_S:
        return reset_at_hint
    parsed = parse_snapshot(snapshot)
    windows = parsed[3] if parsed else resource.usage_windows
    if blocking := _blocking(windows, now):
        return max(w.resets_at for w in blocking)
    if upcoming := [w.resets_at for w in windows if w.resets_at > now]:
        return min(upcoming)
    return now + RATE_LIMIT_COOLDOWN_S


def record_snapshot(store, resource: Resource, raw: dict, *, task_id: str = "") -> None:
    parsed = parse_snapshot(raw)
    if parsed is None:
        return
    plan, source, captured_at, windows = parsed
    store.put(
        LimitEvent(
            workspace_id=resource.workspace_id,
            machine_id=resource.machine_id,
            runner_id=resource.runner_id,
            backend=resource.backend,
            kind="snapshot",
            at=captured_at,
            plan=plan,
            source=source,
            windows=windows,
            task_id=task_id,
        )
    )


def record_exhaustion(
    store, resource: Resource, *, at: float, text: str, reset_at_hint: float, task_id: str
) -> None:
    store.put(
        LimitEvent(
            workspace_id=resource.workspace_id,
            machine_id=resource.machine_id,
            runner_id=resource.runner_id,
            backend=resource.backend,
            kind="exhausted",
            at=at,
            source="error_text",
            text=text[:2000],
            reset_at_hint=reset_at_hint,
            task_id=task_id,
        )
    )


# --- estimation & reporting --------------------------------------------------


def _finished_tasks(store, resource: Resource) -> list[Task]:
    tasks = store.list(
        Task,
        workspace_id=resource.workspace_id,
        runner_id=resource.runner_id,
        backend=resource.backend,
        limit=_TASK_SCAN_LIMIT,
    )
    return [t for t in tasks if t.finished_at > 0]


def _tokens_between(tasks: list[Task], start: float, end: float) -> int:
    return sum(
        t.input_tokens + t.output_tokens for t in tasks if start < t.finished_at <= end
    )


def estimate_window_budgets(events: list[LimitEvent], tasks: list[Task]) -> dict[str, int]:
    """Learned total-window budget in tokens, per window kind.

    Consecutive snapshot events inside the same window (same reset moment)
    say "these many tokens moved the gauge that many percent"; extrapolating
    to 100% estimates the whole window's budget. The median across pairs
    resists the noise of usage from outside Hive, which moves the gauge
    without moving our token counts — estimates are therefore lower bounds
    and tighten as more of the account's usage flows through Hive."""
    snapshots = sorted((e for e in events if e.kind == "snapshot"), key=lambda e: e.at)
    samples: dict[str, list[float]] = {}
    for previous, current in zip(snapshots, snapshots[1:]):
        tokens = _tokens_between(tasks, previous.at, current.at)
        if tokens <= 0:
            continue
        prev_by_kind = {w.kind: w for w in previous.windows}
        for window in current.windows:
            prev = prev_by_kind.get(window.kind)
            if prev is None or abs(prev.resets_at - window.resets_at) > 120:
                continue  # the window rolled over between the two snapshots
            delta_percent = window.used_percent - prev.used_percent
            if delta_percent >= MATERIAL_PERCENT_DELTA:
                samples.setdefault(window.kind, []).append(tokens / (delta_percent / 100.0))
    return {kind: int(median(values)) for kind, values in samples.items() if values}


def resource_limits(store, resource: Resource, *, now: float | None = None) -> dict:
    """Everything Hive knows about one agent's license limits right now."""
    now = time.time() if now is None else now
    events = store.list(
        LimitEvent,
        workspace_id=resource.workspace_id,
        machine_id=resource.machine_id,
        backend=resource.backend,
        limit=_EVENT_SCAN_LIMIT,
    )
    tasks = _finished_tasks(store, resource)
    budgets = estimate_window_budgets(events, tasks)
    windows = []
    for window in resource.usage_windows:
        window_start = (
            window.resets_at - window.window_minutes * 60 if window.window_minutes else 0.0
        )
        hive_tokens = _tokens_between(tasks, window_start, now) if window_start else 0
        row = {
            **window.model_dump(),
            "hive_tokens_in_window": hive_tokens,
            "estimated_budget_tokens": budgets.get(window.kind, 0),
        }
        if budgets.get(window.kind) and window.used_percent < 100:
            row["estimated_tokens_left"] = int(
                budgets[window.kind] * (100.0 - window.used_percent) / 100.0
            )
        windows.append(row)
    exhaustions = sorted((e for e in events if e.kind == "exhausted"), key=lambda e: e.at)
    last = exhaustions[-1] if exhaustions else None
    return {
        "backend": resource.backend,
        "machine_id": resource.machine_id,
        "resource_id": resource.id,
        "plan": resource.usage_plan,
        "source": resource.usage_source,
        "captured_at": resource.usage_captured_at,
        "snapshot_age_s": max(0.0, now - resource.usage_captured_at)
        if resource.usage_captured_at
        else 0.0,
        "windows": windows,
        "cooldown_until": resource.cooldown_until if resource.cooldown_until > now else 0.0,
        "exhaustions_seen": len(exhaustions),
        "last_exhaustion": {
            "at": last.at,
            "text": last.text.splitlines()[0][:200] if last.text else "",
            "reset_at_hint": last.reset_at_hint,
        }
        if last
        else None,
    }


def limits_view(store, workspace_id: str, groups) -> list[dict]:
    """Per-agent license usage for `hive show limits`: provider-reported
    windows, empirical token counts, and learned budgets. Agents whose
    backend exposes nothing and never exhausted are listed bare so the
    operator sees the blind spots too."""
    rows = []
    for group in groups:
        for resource in group.resources:
            row = resource_limits(store, resource)
            row["machine"] = group.machine.name
            rows.append(row)
    rows.sort(key=lambda r: (r["machine"], r["backend"]))
    return rows
