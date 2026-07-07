"""License-usage estimation: reset-hint parsing, provider snapshot mapping,
cooldown selection, and the empirical window-budget estimator.

Fixtures marked "captured live" are real payloads/messages observed on this
fleet (raven laptop + hive-vm), so these tests pin the actual provider
formats, not our guess of them.
"""

import time

from hive._control.limits import (
    EXHAUSTED_PERCENT,
    RATE_LIMIT_COOLDOWN_S,
    USAGE_WINDOW_PREFIX,
    apply_snapshot,
    cooldown_after_exhaustion,
    estimate_window_budgets,
    parse_snapshot,
)
from hive.models import LimitEvent, Resource, Task, UsageWindow
from hive.agents.usage import (
    _claude_windows,
    _codex_snapshot,
    _codex_usage,
    parse_reset_hint,
)

NOW = 1_783_357_000.0

# --- real provider payloads (captured live 2026-07-06) -----------------------

CLAUDE_OAUTH_USAGE = {
    "five_hour": {"utilization": 6.0, "resets_at": "2026-07-06T21:39:59.896901+00:00"},
    "seven_day": {"utilization": 39.0, "resets_at": "2026-07-09T01:59:59.896921+00:00"},
    "limits": [
        {"kind": "session", "group": "session", "percent": 6, "severity": "normal",
         "resets_at": "2026-07-06T21:39:59.896901+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 39, "severity": "normal",
         "resets_at": "2026-07-09T01:59:59.896921+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 67, "severity": "normal",
         "resets_at": "2026-07-09T01:59:59.897243+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
         "is_active": True},
    ],
}

CODEX_RATE_LIMITS = {
    "limit_id": "codex",
    "limit_name": None,
    "primary": {"used_percent": 1.0, "window_minutes": 300, "resets_at": 1783374617},
    "secondary": {"used_percent": 0.0, "window_minutes": 10080, "resets_at": 1783934280},
    "credits": None,
    "individual_limit": None,
    "plan_type": "plus",
    "rate_limit_reached_type": None,
}


# --- parse_reset_hint --------------------------------------------------------


def test_reset_hint_claude_legacy_epoch():
    """The old non-interactive Claude limit message carries the reset epoch
    verbatim after a pipe — the most precise hint there is."""
    assert parse_reset_hint("Claude AI usage limit reached|1783380000", now=NOW) == 1783380000.0


def test_reset_hint_epoch_milliseconds_normalized():
    hint = parse_reset_hint("usage limit reached|1783380000000", now=NOW)
    assert hint == 1783380000.0


def test_reset_hint_codex_try_again_at_clock():
    # Captured live: codex 5h-window exhaustion names a local clock time.
    text = (
        "You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
        "to purchase more credits or try again at 3:28 PM."
    )
    hint = parse_reset_hint(text, now=NOW)
    assert hint > NOW
    assert time.localtime(hint).tm_hour == 15 and time.localtime(hint).tm_min == 28


def test_reset_hint_codex_retry_after_seconds():
    # Captured from codex API logs: fractional seconds after "try again after".
    text = (
        '{"error": {"message": "You\'ve exceeded the rate limit, please slow down '
        'and try again after 60.616292 seconds.", "code": "rate_limit_exceeded"}}'
    )
    assert abs(parse_reset_hint(text, now=NOW) - (NOW + 60.616292)) < 1.0


def test_reset_hint_duration_hours_minutes():
    hint = parse_reset_hint("Upgrade to Pro or try again in 4 hours 58 minutes.", now=NOW)
    assert abs(hint - (NOW + 4 * 3600 + 58 * 60)) < 1.0


def test_reset_hint_claude_tui_clock_with_separator():
    """Claude Code renders '5-hour limit reached ∙ resets 3am' — the separator
    glyph and the bare am/pm clock must both survive parsing."""
    hint = parse_reset_hint("5-hour limit reached ∙ resets 3am", now=NOW)
    assert hint > NOW
    assert time.localtime(hint).tm_hour == 3


def test_reset_hint_weekday():
    hint = parse_reset_hint("Weekly limit reached · resets Thursday at 9am", now=NOW)
    assert hint > NOW
    parsed = time.localtime(hint)
    assert parsed.tm_wday == 3 and parsed.tm_hour == 9  # Thursday


def test_reset_hint_gemini_retry_delay():
    text = '429 RESOURCE_EXHAUSTED ... "retryDelay": "27s"'
    assert abs(parse_reset_hint(text, now=NOW) - (NOW + 27)) < 1.0


def test_reset_hint_iso_timestamp_needs_reset_anchor():
    """ISO timestamps parse only next to a reset word: error text is full of
    log-line timestamps that say nothing about limits."""
    anchored = parse_reset_hint("resets at 2026-07-06T21:39:59+00:00", now=NOW)
    assert anchored == 1783373999.0  # 2026-07-06T21:39:59Z
    assert parse_reset_hint("error at 2026-07-06T21:39:59Z while fetching", now=NOW) == 0.0


def test_reset_hint_garbage_and_past_times_yield_zero():
    """The parser never guesses: no time info, vague durations, past epochs,
    and far-future parses (beyond a weekly window) all return 0.0 so the
    caller falls back to its own cooldown policy."""
    for text in (
        "AssertionError: expected 2 got 3",
        "try again in a few minutes",
        "usage limit reached|1000000000",  # 2001 — long past
        "try again in 400 days",  # beyond any real window
        "",
    ):
        assert parse_reset_hint(text, now=NOW) == 0.0, text


# --- provider snapshot mapping ----------------------------------------------


def test_claude_oauth_payload_maps_all_windows():
    windows = _claude_windows(CLAUDE_OAUTH_USAGE)
    by_kind = {w["kind"]: w for w in windows}
    assert set(by_kind) == {"session", "weekly", "weekly_fable"}
    assert by_kind["session"]["used_percent"] == 6.0
    assert by_kind["session"]["window_minutes"] == 300
    assert by_kind["weekly"]["window_minutes"] == 10080
    assert by_kind["weekly_fable"]["used_percent"] == 67.0
    # ISO reset times become epochs the chief can compare against time.time().
    assert abs(by_kind["session"]["resets_at"] - 1783373999.896901) < 1.0


def test_claude_oauth_payload_without_limits_list_falls_back():
    legacy = {k: CLAUDE_OAUTH_USAGE[k] for k in ("five_hour", "seven_day")}
    by_kind = {w["kind"]: w for w in _claude_windows(legacy)}
    assert set(by_kind) == {"session", "weekly"}
    assert by_kind["weekly"]["used_percent"] == 39.0


def test_codex_rate_limits_map_to_windows():
    snap = _codex_snapshot(CODEX_RATE_LIMITS, "2026-07-06T09:18:00.159Z")
    assert snap["plan"] == "plus"
    by_kind = {w["kind"]: w for w in snap["windows"]}
    assert by_kind["session"]["resets_at"] == 1783374617.0
    assert by_kind["weekly"]["window_minutes"] == 10080
    assert snap["captured_at"] == 1783329480.159  # 2026-07-06T09:18:00.159Z


def test_codex_reached_limit_marks_severity():
    reached = {**CODEX_RATE_LIMITS, "rate_limit_reached_type": "primary"}
    by_kind = {w["kind"]: w for w in _codex_snapshot(reached, "")["windows"]}
    assert by_kind["session"]["severity"] == "exceeded"
    assert by_kind["weekly"]["severity"] == ""


def test_codex_usage_reads_newest_rollout(tmp_path):
    """The collector round-trips through a real rollout-file layout: it finds
    the newest session file and takes its last rate_limits event."""
    import json

    day = tmp_path / "2026" / "07" / "06"
    day.mkdir(parents=True)
    stale = {**CODEX_RATE_LIMITS, "primary": {**CODEX_RATE_LIMITS["primary"], "used_percent": 55.0}}
    lines = [
        {"timestamp": "2026-07-06T09:00:00.000Z", "type": "event_msg",
         "payload": {"type": "token_count", "rate_limits": stale}},
        {"timestamp": "2026-07-06T09:18:00.159Z", "type": "event_msg",
         "payload": {"type": "token_count", "rate_limits": CODEX_RATE_LIMITS}},
    ]
    (day / "rollout-2026-07-06T09-00-00-abc.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines), encoding="utf-8"
    )
    snap = _codex_usage(sessions_dir=tmp_path)
    assert snap is not None
    assert {w["kind"]: w for w in snap["windows"]}["session"]["used_percent"] == 1.0  # last event wins
    assert _codex_usage(sessions_dir=tmp_path / "empty") is None


# --- snapshot application & cooldown ------------------------------------------


def _snapshot(percent: float, resets_at: float, *, kind="session", captured_at=NOW) -> dict:
    return {
        "backend": "claude",
        "plan": "max_5x",
        "source": "oauth",
        "captured_at": captured_at,
        "windows": [
            {"kind": kind, "used_percent": percent, "window_minutes": 300, "resets_at": resets_at}
        ],
    }


def test_apply_snapshot_writes_windows_and_reports_material_change():
    resource = Resource(runner_id="r", backend="claude")
    assert apply_snapshot(resource, _snapshot(40.0, NOW + 3600), now=NOW) is True
    assert resource.usage_plan == "max_5x"
    assert resource.usage_windows[0].used_percent == 40.0

    # Same numbers again (a later heartbeat): applied but not material.
    again = _snapshot(40.0, NOW + 3600, captured_at=NOW + 60)
    assert apply_snapshot(resource, again, now=NOW + 60) is False
    assert resource.usage_captured_at == NOW + 60

    # The gauge moved: material again.
    moved = _snapshot(45.0, NOW + 3600, captured_at=NOW + 120)
    assert apply_snapshot(resource, moved, now=NOW + 120) is True


def test_apply_snapshot_ignores_stale_and_garbage():
    """Out-of-order delivery and malformed dicts must never regress the
    resource: latest captured_at wins, garbage is a no-op."""
    resource = Resource(runner_id="r", backend="claude")
    apply_snapshot(resource, _snapshot(40.0, NOW + 3600), now=NOW)
    older = _snapshot(10.0, NOW + 3600, captured_at=NOW - 500)
    assert apply_snapshot(resource, older, now=NOW) is False
    assert resource.usage_windows[0].used_percent == 40.0
    for garbage in ({}, {"windows": "nope"}, {"windows": [{"kind": 3}], "captured_at": NOW + 999}):
        assert apply_snapshot(resource, garbage, now=NOW) is False
    assert parse_snapshot({"captured_at": NOW, "windows": []}) is None


def test_spent_window_cools_resource_until_its_own_reset():
    resource = Resource(runner_id="r", backend="claude")
    reset = NOW + 2 * 3600
    apply_snapshot(resource, _snapshot(EXHAUSTED_PERCENT, reset), now=NOW)
    assert resource.cooldown_until == reset
    assert resource.last_exhaustion_text.startswith(USAGE_WINDOW_PREFIX)


def test_healthy_snapshot_clears_snapshot_cooldown_but_not_error_cooldown():
    """Self-healing: a fresh gauge below the threshold clears a cooldown the
    gauge itself created — but never one an actual task failure created,
    because the gauge can lag the enforcement."""
    resource = Resource(runner_id="r", backend="claude")
    apply_snapshot(resource, _snapshot(99.0, NOW + 3600), now=NOW)
    assert resource.cooldown_until > NOW
    apply_snapshot(resource, _snapshot(5.0, NOW + 9000, captured_at=NOW + 60), now=NOW + 60)
    assert resource.cooldown_until == 0.0

    resource.mark_exhausted(until=NOW + 3600, at=NOW, text="429 too many requests", task_id="t")
    apply_snapshot(resource, _snapshot(5.0, NOW + 9000, captured_at=NOW + 120), now=NOW + 120)
    assert resource.cooldown_until == NOW + 3600  # error cooldown untouched


def test_cooldown_after_exhaustion_preference_order():
    """Best evidence wins: the message's own reset time, else the spent
    window's reset, else the soonest upcoming rollover, else the flat hour."""
    resource = Resource(runner_id="r", backend="claude")

    # 1. explicit hint from the error text
    until = cooldown_after_exhaustion(
        resource, reset_at_hint=NOW + 1234, snapshot={}, now=NOW
    )
    assert until == NOW + 1234

    # 2. binding (spent) window from the snapshot
    until = cooldown_after_exhaustion(
        resource, reset_at_hint=0.0, snapshot=_snapshot(100.0, NOW + 5000), now=NOW
    )
    assert until == NOW + 5000

    # 3. soonest upcoming rollover when no window looks spent (the gauge lags)
    until = cooldown_after_exhaustion(
        resource, reset_at_hint=0.0, snapshot=_snapshot(6.0, NOW + 5000), now=NOW
    )
    assert until == NOW + 5000

    # 4. nothing known: flat fallback
    until = cooldown_after_exhaustion(resource, reset_at_hint=0.0, snapshot={}, now=NOW)
    assert until == NOW + RATE_LIMIT_COOLDOWN_S


def test_cooldown_ignores_hint_in_the_past_or_too_far_out():
    resource = Resource(runner_id="r", backend="claude")
    for bad_hint in (NOW - 10, NOW + 30 * 86400):
        until = cooldown_after_exhaustion(resource, reset_at_hint=bad_hint, snapshot={}, now=NOW)
        assert until == NOW + RATE_LIMIT_COOLDOWN_S


# --- empirical window-budget estimation ---------------------------------------


def _snapshot_event(at: float, percent: float, resets_at: float) -> LimitEvent:
    return LimitEvent(
        backend="claude",
        kind="snapshot",
        at=at,
        windows=[UsageWindow(kind="session", used_percent=percent, resets_at=resets_at)],
    )


def _task(finished_at: float, tokens: int) -> Task:
    return Task(
        project_id="p", workstream_id="w", repo="r", instructions="",
        finished_at=finished_at, input_tokens=tokens, output_tokens=0,
    )


def test_budget_estimated_from_percent_deltas():
    """10k tokens moved the gauge 10% → the whole window is ~100k tokens.
    Multiple consistent pairs agree, and the estimate survives scaling: twice
    the tokens for the same delta doubles the budget."""
    reset = NOW + 9000
    events = [
        _snapshot_event(NOW, 10.0, reset),
        _snapshot_event(NOW + 100, 20.0, reset),
        _snapshot_event(NOW + 200, 30.0, reset),
    ]
    tasks = [_task(NOW + 50, 10_000), _task(NOW + 150, 10_000)]
    assert estimate_window_budgets(events, tasks) == {"session": 100_000}

    doubled = [_task(NOW + 50, 20_000), _task(NOW + 150, 20_000)]
    assert estimate_window_budgets(events, doubled) == {"session": 200_000}


def test_budget_skips_pairs_across_window_rollover():
    """A snapshot pair straddling a reset compares different windows — the
    percent drop would poison the estimate, so those pairs are excluded."""
    events = [
        _snapshot_event(NOW, 90.0, NOW + 100),
        _snapshot_event(NOW + 200, 5.0, NOW + 18100),  # new window after reset
    ]
    tasks = [_task(NOW + 150, 50_000)]
    assert estimate_window_budgets(events, tasks) == {}


def test_budget_needs_tokens_and_movement():
    reset = NOW + 9000
    no_tokens = estimate_window_budgets(
        [_snapshot_event(NOW, 10.0, reset), _snapshot_event(NOW + 100, 20.0, reset)], []
    )
    assert no_tokens == {}
    no_movement = estimate_window_budgets(
        [_snapshot_event(NOW, 10.0, reset), _snapshot_event(NOW + 100, 10.5, reset)],
        [_task(NOW + 50, 10_000)],
    )
    assert no_movement == {}


# --- end-to-end through the runner protocol ------------------------------------


def test_snapshots_and_exhaustion_flow_through_the_api(tmp_path):
    """Full loop over the real protocol: a runner registers with a usage
    snapshot (windows land on the resource), a task hits a rate limit with a
    parsed reset hint (cooldown honors the hint, history records the event),
    and `GET /api/show` exposes it all under `limits`."""
    from fastapi.testclient import TestClient

    from hive.api import create_app
    from hive.config.settings import Config
    from hive.persistence.store import MemoryStore
    from hive._control.supervisor import Supervisor

    store = MemoryStore()
    supervisor = Supervisor(store, lambda project_id, events: None)
    config = Config(
        gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="",
        orch_model="", runner_token="test-token", data_dir=tmp_path,
    )
    client = TestClient(create_app(store, supervisor, config))
    headers = {"X-Hive-Token": "test-token"}

    reset = time.time() + 7200
    rid = client.post(
        "/api/runners/register",
        json={
            "name": "raven",
            "backends": ["claude"],
            "usage_snapshots": {"claude": _snapshot(40.0, reset, captured_at=time.time())},
        },
        headers=headers,
    ).json()["runner_id"]

    resource = store.list(Resource)[-1]
    assert resource.usage_windows[0].used_percent == 40.0
    assert store.list(LimitEvent, kind="snapshot")  # material first snapshot recorded

    # Give the runner a task, then have it fail on a rate limit with a hint.
    task = store.put(
        Task(project_id="p", workstream_id="w", repo="probe:local", instructions="x",
             backend="claude", runner_id=rid, status="running")
    )
    hint = time.time() + 5400
    client.post(
        f"/api/tasks/{task.id}/result",
        json={
            "text": "5-hour limit reached ∙ resets 6pm",
            "is_error": True,
            "resource_exhausted": True,
            "reset_at_hint": hint,
            "usage_snapshot": _snapshot(100.0, reset, captured_at=time.time()),
        },
        headers=headers,
    ).raise_for_status()

    resource = store.get(Resource, resource.id)
    assert resource.cooldown_until == hint  # the message's own reset wins
    exhausted = store.list(LimitEvent, kind="exhausted")
    assert len(exhausted) == 1 and exhausted[0].reset_at_hint == hint

    limits = client.get("/api/show").json()["limits"]
    row = next(r for r in limits if r["backend"] == "claude")
    assert row["cooldown_until"] == hint
    assert row["exhaustions_seen"] == 1
    assert row["windows"][0]["used_percent"] == 100.0
