"""Regression coverage for account usage, across the CLI and rollout seams."""

import json
import os
import sys
from datetime import datetime

from hive.agents import collect_usage


def _rollout(root, name, timestamp, limits, *, modified):
    day = root / ".codex" / "sessions" / "2026" / "09" / "08"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-{name}.jsonl"
    path.write_text(json.dumps({"timestamp": timestamp, "payload": {"rate_limits": limits}}) + "\n")
    os.utime(path, (modified, modified))


def _limits(bucket, used, minutes=10080):
    return {
        "limit_id": bucket,
        "primary": {"used_percent": used, "window_minutes": minutes, "resets_at": 1789449759},
        "secondary": None,
        "plan_type": "pro",
    }


def test_rollouts_preserve_main_account_bucket_and_observation_time(tmp_path, monkeypatch):
    """A fresh Spark event or touched old file cannot replace the main quota."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "no-cli"))
    _rollout(tmp_path, "main", "2026-09-08T10:40:07Z", _limits("codex", 44), modified=10)
    _rollout(tmp_path, "older-main", "2026-09-08T08:00:00Z", _limits("codex", 29), modified=20)
    _rollout(tmp_path, "spark", "2026-09-08T10:41:00Z", _limits("codex_bengalfox", 0, 300), modified=30)

    snapshot = collect_usage("codex")

    assert snapshot["source"] == "rollout"
    assert snapshot["captured_at"] == datetime.fromisoformat("2026-09-08T10:40:07+00:00").timestamp()
    assert [(w["kind"], w["used_percent"]) for w in snapshot["windows"]] == [("weekly", 44)]


def _codex_cli(tmp_path, monkeypatch, payload):
    """Serve the native CLI protocol over real process pipes, without a login."""
    binary = tmp_path / "codex"
    binary.write_text(f"""#!{sys.executable}
import json, sys
assert sys.argv[1:] == ['app-server', '--stdio']
request = json.loads(sys.stdin.readline())
assert request['method'] == 'initialize'
print(json.dumps({{'id': request['id'], 'result': {{}}}}), flush=True)
assert json.loads(sys.stdin.readline())['method'] == 'initialized'
request = json.loads(sys.stdin.readline())
assert request['method'] == 'account/rateLimits/read'
print(json.dumps({{'method': 'account/updated', 'params': {{}}}}), flush=True)
print(json.dumps({{'id': request['id'], 'result': {payload!r}}}), flush=True)
sys.stdin.read()
""")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_live_cli_reads_main_quota_without_starting_a_turn(tmp_path, monkeypatch):
    """A current main-account response outranks stale logs and the Spark bucket."""
    main = {"limitId": "codex", "planType": "pro", "primary": {
        "usedPercent": 44, "windowDurationMins": 10080, "resetsAt": 1789449759,
    }, "secondary": None}
    spark = {"limitId": "codex_bengalfox", "primary": {
        "usedPercent": 0, "windowDurationMins": 300, "resetsAt": 1788882060,
    }, "secondary": None}
    _codex_cli(tmp_path, monkeypatch, {"rateLimits": spark, "rateLimitsByLimitId": {
        "codex_bengalfox": spark, "codex": main,
    }})
    _rollout(tmp_path, "old", "2026-09-08T08:00:00Z", _limits("codex", 12), modified=10)

    snapshot = collect_usage("codex")

    assert snapshot["source"] == "app-server"
    assert snapshot["plan"] == "pro"
    assert [(w["kind"], w["used_percent"]) for w in snapshot["windows"]] == [("weekly", 44)]
    assert snapshot["captured_at"] > datetime.fromisoformat("2026-09-08T08:00:00+00:00").timestamp()


def test_live_missing_usage_does_not_become_zero_or_reuse_old_windows(tmp_path, monkeypatch):
    """A present bucket with absent measurements clears stale recorded windows."""
    _codex_cli(tmp_path, monkeypatch, {"rateLimitsByLimitId": {"codex": {
        "limitId": "codex", "primary": {"windowDurationMins": 300, "usedPercent": None},
        "secondary": {"windowDurationMins": 10080},
    }}})
    _rollout(tmp_path, "old", "2026-09-08T08:00:00Z", _limits("codex", 12), modified=10)

    snapshot = collect_usage("codex")

    assert snapshot["source"] == "app-server"
    assert snapshot["windows"] == []


def test_foreign_only_usage_is_not_general_codex_usage(tmp_path, monkeypatch):
    """Neither native telemetry nor logs can relabel Spark as the main account."""
    spark = {"limitId": "codex_bengalfox", "primary": {
        "usedPercent": 0, "windowDurationMins": 300,
    }}
    _codex_cli(tmp_path, monkeypatch, {"rateLimits": spark, "rateLimitsByLimitId": {
        "codex_bengalfox": spark,
    }})
    _rollout(tmp_path, "spark", "2026-09-08T10:41:00Z", _limits("codex_bengalfox", 0), modified=10)

    assert collect_usage("codex") is None
