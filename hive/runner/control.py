"""Laptop-local runner controls: the pause flag and the status file.

The pause flag (`runner.paused` in the runner state dir) is the single source
of truth for "this machine's runner is switched off". Three parties act on it:

- the daemon exits before its next poll once the flag appears (a task already
  assigned still finishes and reports first — nothing is abandoned);
- launchd's KeepAlive is conditioned on the flag being absent, so a paused
  runner is not respawned and removing the flag starts it again;
- the menu bar app (`hive.runner.menubar`) toggles it and renders the result.

The status file (`runner.status.json`) is the daemon's side of the
conversation: its pid and what it is doing right now, so local UIs can show
idle/working without talking to the chief. Purely informational; staleness is
detected through the recorded pid.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

PAUSE_FILENAME = "runner.paused"
STATUS_FILENAME = "runner.status.json"


def runner_state_dir() -> Path:
    """Where runner-local state lives (same dir as the chief roster)."""
    return Path(os.environ.get("HIVE_RUNNER_STATE_DIR", "~/.config/hive")).expanduser()


def pause_path(state_dir: Path | None = None) -> Path:
    return (state_dir or runner_state_dir()) / PAUSE_FILENAME


def status_path(state_dir: Path | None = None) -> Path:
    return (state_dir or runner_state_dir()) / STATUS_FILENAME


# -- the pause flag ----------------------------------------------------------


def is_paused(state_dir: Path | None = None) -> bool:
    return pause_path(state_dir).exists()


def request_pause(state_dir: Path | None = None) -> None:
    """Switch the runner off: drain after the current task, don't respawn."""
    path = pause_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"paused at {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n")


def clear_pause(state_dir: Path | None = None) -> None:
    """Switch the runner back on (launchd restarts it once the flag is gone)."""
    pause_path(state_dir).unlink(missing_ok=True)


# -- the status file ---------------------------------------------------------


def write_status(
    state: str,
    *,
    task: dict | None = None,
    chief: str = "",
    state_dir: Path | None = None,
) -> None:
    """Record what this daemon process is doing right now. Atomic replace so a
    concurrent reader never sees a torn file."""
    path = status_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "state": state,  # "idle" | "task" | "paused"
        "task": task or {},
        "chief": chief,
        "since": time.time(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def read_status(state_dir: Path | None = None) -> dict:
    """The last written status, `{}` when missing or unreadable."""
    try:
        data = json.loads(status_path(state_dir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# -- the derived view --------------------------------------------------------


class RunnerMode(StrEnum):
    idle = "idle"  # daemon alive, waiting for tasks
    working = "working"  # daemon alive, task in flight
    draining = "draining"  # pause requested, current task still finishing
    paused = "paused"  # pause flag set, daemon gone
    offline = "offline"  # not paused, but no live daemon either


@dataclass(frozen=True)
class RunnerView:
    """One glanceable verdict for local UIs: what is the runner doing, and
    which line should a human read about it."""

    mode: RunnerMode
    detail: str
    task: dict = field(default_factory=dict)


def _repo_name(url: str) -> str:
    return url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]


def _task_line(status: dict, now: float) -> str:
    task = status.get("task") or {}
    minutes = max(0, int((now - float(status.get("since") or now)) // 60))
    what = task.get("kind") or "task"
    if repo := _repo_name(task.get("repo", "")):
        what += f" on {repo}"
    return f"{what} ({minutes}m)"


def runner_view(state_dir: Path | None = None, *, now: float | None = None) -> RunnerView:
    """Derive the runner's local state from the pause flag + status file.

    Pure with respect to its inputs (files, pid table, `now`), so the menu bar
    app is a thin shell over this one function.
    """
    now = time.time() if now is None else now
    status = read_status(state_dir)
    working = pid_alive(status.get("pid")) and status.get("state") == "task"
    if is_paused(state_dir):
        if working:
            return RunnerView(
                RunnerMode.draining,
                f"Pausing — finishing {_task_line(status, now)}",
                status.get("task") or {},
            )
        return RunnerView(RunnerMode.paused, "Paused — not taking tasks")
    if working:
        return RunnerView(
            RunnerMode.working, f"Working: {_task_line(status, now)}", status.get("task") or {}
        )
    if pid_alive(status.get("pid")):
        return RunnerView(RunnerMode.idle, "Idle — waiting for tasks")
    return RunnerView(RunnerMode.offline, "Runner is not running")
