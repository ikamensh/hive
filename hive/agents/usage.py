"""License-usage introspection: what the agent CLIs know about their own limits.

Two independent capabilities, both best-effort and read-only:

- `parse_reset_hint(text)` — pull an explicit reset time out of a rate-limit
  error message ("resets 3am", "try again in 4 hours 58 minutes",
  "Claude AI usage limit reached|1751749200", gemini's `retryDelay`). Runs on
  the runner because clock-time messages are rendered in the machine's local
  timezone.

- `collect_usage(backend)` — the provider's own usage gauge, without spending
  quota. claude: the Claude Code OAuth token (keychain on macOS, credentials
  file elsewhere) authorizes `GET api.anthropic.com/api/oauth/usage`, which
  returns exact used-percent + reset time for the 5h/weekly windows. codex:
  the native app-server reads current main-account limits, with observed
  main-bucket rollout snapshots as a fallback. Both are account-wide, so they also see
  usage from outside Hive. cursor/gemini-cli expose nothing — empirical
  tracking from error messages is all we get there.

Snapshots are plain dicts shaped like
`{"backend", "plan", "source", "captured_at", "windows": [UsageWindow-shaped]}`
so they travel through task results and register heartbeats unchanged; the
chief validates them into models (`hive._control.limits`).

Run `python -m hive.agents` on any machine to see what it can collect.
"""

from __future__ import annotations

import json
import re
import selectors
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_OAUTH_BETA = "oauth-2025-04-20"
KEYCHAIN_SERVICE = "Claude Code-credentials"

# A parsed reset further out than this is a parse error, not a real window
# (weekly limits reset within 7 days).
RESET_HINT_MAX_S = 8 * 86400

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Anchor words that introduce a reset time in provider messages. Kept apart
# from the time expressions so each combination stays one readable pattern.
_ANCHOR = r"(?:resets?|try\s+again|retry|available\s+again)"

# "Claude AI usage limit reached|1751749200" (epoch, sometimes milliseconds).
_EPOCH_PIPE_RE = re.compile(r"limit\s+reached\s*\|\s*(\d{10,13})", re.IGNORECASE)
# ISO timestamp within reach of an anchor word: "resets at 2026-07-06T21:39:59+00:00".
_ISO_RE = re.compile(
    _ANCHOR + r"\D{0,40}?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)
# "try again in 4 hours 58 minutes" / "resets in 2h3m" / "in 30 seconds".
_DURATION_RE = re.compile(
    _ANCHOR
    + r"\s+(?:in|after)\s+"
    + r"(?:(\d+)\s*d(?:ays?)?)?\s*,?\s*(?:and\s+)?"
    + r"(?:(\d+)\s*h(?:ours?|rs?)?)?\s*,?\s*(?:and\s+)?"
    + r"(?:(\d+(?:\.\d+)?)\s*m(?:in(?:utes?)?)?\b)?\s*,?\s*(?:and\s+)?"
    + r"(?:(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b)?",
    re.IGNORECASE,
)
# "resets Thursday at 9am" — must run before the bare clock pattern so the
# weekday is not lost.
_WEEKDAY_RE = re.compile(
    _ANCHOR
    + r"\s+(?:on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    + r"(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?",
    re.IGNORECASE,
)
# "resets 3am" / "resets at 21:30" / "try again at 10:30 PM".
_CLOCK_RE = re.compile(
    _ANCHOR + r"\s*(?:at\s*)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b"
    "|" + _ANCHOR + r"\s*(?:at\s*)?(\d{1,2}):(\d{2})\b",
    re.IGNORECASE,
)
# Gemini 429 payloads: `"retryDelay": "27s"`; HTTP-ish "retry-after: 3600".
_RETRY_DELAY_RE = re.compile(r"retry.?delay\D{0,5}(\d+(?:\.\d+)?)s", re.IGNORECASE)
_RETRY_AFTER_RE = re.compile(r"retry.?after\D{0,5}(\d+)\b", re.IGNORECASE)


def _next_clock(now_dt: datetime, hour: int, minute: int) -> datetime:
    candidate = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_dt:
        candidate += timedelta(days=1)
    return candidate


def parse_reset_hint(text: str, *, now: float | None = None) -> float:
    """Epoch seconds a rate-limit message says the limit resets, or 0.0.

    Clock times ("resets 3am") are interpreted in this machine's local
    timezone — the CLI that printed them ran here. Anything unparseable, in
    the past, or further out than a weekly window returns 0.0; the caller
    falls back to its own cooldown policy.
    """
    now = time.time() if now is None else now
    normalized = re.sub(r"[∙·•—–]", " ", text)
    hint = 0.0

    if m := _EPOCH_PIPE_RE.search(normalized):
        epoch = float(m.group(1))
        hint = epoch / 1000.0 if epoch > 1e12 else epoch
    elif m := _ISO_RE.search(normalized):
        try:
            stamp = m.group(1).replace(" ", "T").replace("Z", "+00:00")
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()  # message rendered in local time
            hint = parsed.timestamp()
        except ValueError:
            hint = 0.0
    elif (m := _DURATION_RE.search(normalized)) and any(m.groups()):
        days, hours, minutes, seconds = (float(g) if g else 0.0 for g in m.groups())
        hint = now + days * 86400 + hours * 3600 + minutes * 60 + seconds
    elif m := _WEEKDAY_RE.search(normalized):
        now_dt = datetime.fromtimestamp(now).astimezone()
        target_day = _WEEKDAYS.index(m.group(1).lower())
        hour = int(m.group(2)) if m.group(2) else 0
        if (m.group(4) or "").lower() == "pm" and hour != 12:
            hour += 12
        if (m.group(4) or "").lower() == "am" and hour == 12:
            hour = 0
        candidate = now_dt.replace(
            hour=hour, minute=int(m.group(3) or 0), second=0, microsecond=0
        ) + timedelta(days=(target_day - now_dt.weekday()) % 7)
        if candidate <= now_dt:
            candidate += timedelta(days=7)
        hint = candidate.timestamp()
    elif m := _CLOCK_RE.search(normalized):
        now_dt = datetime.fromtimestamp(now).astimezone()
        if m.group(3):  # am/pm form
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            if m.group(3).lower() == "pm" and hour != 12:
                hour += 12
            if m.group(3).lower() == "am" and hour == 12:
                hour = 0
        else:  # 24h form
            hour, minute = int(m.group(4)), int(m.group(5))
        if hour < 24:
            hint = _next_clock(now_dt, hour, minute).timestamp()
    elif m := _RETRY_DELAY_RE.search(normalized) or _RETRY_AFTER_RE.search(normalized):
        hint = now + float(m.group(1))

    if now < hint <= now + RESET_HINT_MAX_S:
        return hint
    return 0.0


# --- claude: the Claude Code OAuth usage endpoint ---------------------------


def _claude_credentials() -> tuple[str, str]:
    """(access token, plan tier) from Claude Code's stored OAuth credential,
    or ("", "") when unavailable/expired.

    macOS keeps it in the login keychain; elsewhere Claude Code writes
    `~/.claude/.credentials.json`. An expired token is treated as absent —
    Claude Code owns the refresh cycle, and the next task on this machine
    refreshes it as a side effect.
    """
    raw = ""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            raw = proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not raw:
        try:
            raw = (Path.home() / ".claude" / ".credentials.json").read_text()
        except OSError:
            return "", ""
    try:
        oauth = json.loads(raw).get("claudeAiOauth", {})
    except json.JSONDecodeError:
        return "", ""
    expires_ms = oauth.get("expiresAt") or 0
    if expires_ms and expires_ms / 1000.0 < time.time() + 60:
        return "", ""
    plan = oauth.get("rateLimitTier") or oauth.get("subscriptionType") or ""
    return oauth.get("accessToken", ""), plan


def _claude_windows(payload: dict) -> list[dict]:
    """Map the oauth/usage payload to UsageWindow-shaped dicts.

    Prefers the `limits` list (per-window kind/percent/severity, including
    model-scoped weekly caps); falls back to the flat five_hour/seven_day
    fields on older payloads.
    """
    kind_minutes = {"session": 300, "weekly": 10080}
    windows = []
    for entry in payload.get("limits") or []:
        scoped_model = (entry.get("scope") or {}).get("model") or {}
        scope = scoped_model.get("id") or scoped_model.get("display_name") or ""
        kind = {"session": "session", "weekly_all": "weekly"}.get(entry.get("kind", ""))
        if kind is None:  # scoped: name the scope so windows stay distinguishable
            kind = f"weekly_{scope.lower()}" if scope else entry.get("kind", "other")
        windows.append(
            {
                "kind": kind,
                "model_scope": scope.lower().replace(" ", "-"),
                "used_percent": float(entry.get("percent") or 0.0),
                "window_minutes": kind_minutes.get(entry.get("group", ""), 0)
                or kind_minutes.get(kind, 0),
                "resets_at": _iso_epoch(entry.get("resets_at")),
                "severity": entry.get("severity") or "",
            }
        )
    if not windows:
        for key, kind, scope in (
            ("five_hour", "session", ""), ("seven_day", "weekly", ""),
            *((f"seven_day_{family}", f"weekly_{family}", family)
              for family in ("fable", "opus", "sonnet", "haiku")),
        ):
            block = payload.get(key) or {}
            if not block:
                continue
            windows.append(
                {
                    "kind": kind,
                    "model_scope": scope,
                    "used_percent": float(block.get("utilization") or 0.0),
                    "window_minutes": kind_minutes["weekly" if scope else kind],
                    "resets_at": _iso_epoch(block.get("resets_at")),
                    "severity": "",
                }
            )
    return windows


def _iso_epoch(stamp: str | None) -> float:
    if not stamp:
        return 0.0
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _claude_usage() -> dict | None:
    token, plan = _claude_credentials()
    if not token:
        return None
    try:
        response = httpx.get(
            CLAUDE_USAGE_URL,
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": CLAUDE_OAUTH_BETA},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    windows = _claude_windows(payload)
    if not windows:
        return None
    return {
        "backend": "claude",
        "plan": plan,
        "source": "oauth",
        "captured_at": time.time(),
        "windows": windows,
    }


# --- codex: live account limits, with observed rollout fallback --------------


def _codex_account_limits() -> dict | None:
    """Read native account telemetry without starting a thread or model turn."""
    proc = subprocess.Popen(
        ["codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, bufsize=0,
    )
    deadline = time.monotonic() + 15
    buffered = b""
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)

            def send(message: dict) -> None:
                proc.stdin.write(json.dumps(message).encode() + b"\n")
                proc.stdin.flush()

            def request(request_id: int, method: str, **params) -> dict:
                nonlocal buffered
                send({"id": request_id, "method": method, "params": params})
                while True:
                    while b"\n" in buffered:
                        line, buffered = buffered.split(b"\n", 1)
                        message = json.loads(line)
                        if message.get("id") == request_id:
                            if "error" in message:
                                raise ValueError("Codex account telemetry request failed")
                            return message["result"]
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise subprocess.TimeoutExpired(proc.args, 15)
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        raise ValueError("Codex account telemetry stream closed")
                    buffered += chunk

            request(1, "initialize", clientInfo={"name": "hive_usage", "version": "1"})
            send({"method": "initialized"})
            payload = request(2, "account/rateLimits/read")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()

    buckets = payload.get("rateLimitsByLimitId")
    limits = buckets.get("codex") if buckets is not None else payload.get("rateLimits")
    if not limits or limits.get("limitId") not in (None, "codex"):
        return None
    return _codex_snapshot(limits, "") | {"source": "app-server", "captured_at": time.time()}


def _codex_usage(sessions_dir: Path | None = None) -> dict | None:
    """Prefer live main-account telemetry; retain a fallback's observation age."""
    if sessions_dir is None:
        try:
            live = _codex_account_limits()
            if live is not None:
                return live
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    root = sessions_dir or Path.home() / ".codex" / "sessions"
    files = sorted(root.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime)
    found: dict | None = None
    newest = 0.0
    for path in reversed(files[-20:]):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    limits = (event.get("payload") or {}).get("rate_limits") or {}
                    # Spark and other model buckets are independent allowances.
                    # A newer foreign event must never become general Codex usage.
                    if not limits or limits.get("limit_id") not in (None, "codex"):
                        continue
                    timestamp = event.get("timestamp", "")
                    captured_at = _iso_epoch(timestamp)
                    if captured_at > newest:
                        found = {"limits": limits, "timestamp": timestamp}
                        newest = captured_at
        except OSError:
            continue
    return _codex_snapshot(found["limits"], found["timestamp"]) if found else None


def _codex_snapshot(limits: dict, timestamp: str) -> dict:
    windows = []
    for key in ("primary", "secondary"):
        block = limits.get(key) or {}
        used_percent = block.get("used_percent", block.get("usedPercent"))
        if used_percent is None:
            continue
        window_minutes = int(block.get("window_minutes", block.get("windowDurationMins")) or 0)
        windows.append(
            {
                # Codex can put a weekly window in either native slot.
                "kind": {300: "session", 10080: "weekly"}.get(window_minutes, key),
                "used_percent": float(used_percent),
                "window_minutes": window_minutes,
                "resets_at": float(block.get("resets_at", block.get("resetsAt")) or 0.0),
                "severity": "exceeded" if limits.get("rate_limit_reached_type", limits.get("rateLimitReachedType")) == key else "",
            }
        )
    return {
        "backend": "codex",
        "plan": limits.get("plan_type", limits.get("planType")) or "",
        "source": "rollout",
        "captured_at": _iso_epoch(timestamp),
        "windows": windows,
    }


_COLLECTORS = {"claude": _claude_usage, "codex": _codex_usage}


def collect_usage(backend: str) -> dict | None:
    """Best-effort usage snapshot for `backend`; None when the backend exposes
    nothing (cursor, gemini-cli) or collection failed. Never raises — a broken
    gauge must not take down task execution."""
    collector = _COLLECTORS.get(backend)
    if collector is None:
        return None
    try:
        return collector()
    except Exception:  # noqa: BLE001 — introspection is strictly optional
        return None


if __name__ == "__main__":
    for name in sorted(_COLLECTORS):
        snapshot = collect_usage(name)
        print(f"{name}: {json.dumps(snapshot, indent=1) if snapshot else 'no usage source'}")
