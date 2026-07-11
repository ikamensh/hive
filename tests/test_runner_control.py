"""The laptop off-switch contract (hive.runner.control + menubar + daemon).

Properties pinned, chosen to survive refactors:
- the pause flag round-trips (request -> visible -> clear) and both writes are
  idempotent;
- a written status reads back with this process's pid (the liveness anchor);
- corrupt/missing status degrades to `{}` — a broken file must never take the
  menu bar app down;
- runner_view() reaches every RunnerMode from (pause flag, status file, pid
  liveness), including `draining` (paused while a task runs);
- the menu bar app has a title and toggle label for every mode, so no state
  can render as a KeyError;
- a paused daemon exits at startup without ever building the worker loop.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from hive.runner import control
from hive.runner.control import RunnerMode


def _dead_pid() -> int:
    """A pid that provably belonged to a finished process."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_pause_flag_roundtrip_and_idempotence(tmp_path):
    assert not control.is_paused(tmp_path)
    control.request_pause(tmp_path)
    control.request_pause(tmp_path)
    assert control.is_paused(tmp_path)
    control.clear_pause(tmp_path)
    control.clear_pause(tmp_path)
    assert not control.is_paused(tmp_path)


def test_status_roundtrips_and_carries_this_pid(tmp_path):
    task = {"id": "t1", "kind": "issue", "repo": "https://github.com/o/widget.git"}
    control.write_status("task", task=task, chief="http://chief", state_dir=tmp_path)
    status = control.read_status(tmp_path)
    assert status["pid"] == os.getpid()
    assert status["state"] == "task"
    assert status["task"] == task
    assert status["chief"] == "http://chief"


def test_status_slow_facts_carry_over_between_writes(tmp_path):
    """chief/backends/last_task persist across writes that omit them, so the
    'task started' write can't erase what discovery and the previous task
    established; state/task/since always describe the newest write."""
    control.write_status("idle", chief="http://chief", backends=["claude"], state_dir=tmp_path)
    control.write_status(
        "idle",
        last_task={"kind": "review", "repo": "r", "is_error": False, "finished_at": 1.0},
        state_dir=tmp_path,
    )
    control.write_status("task", task={"id": "t2"}, state_dir=tmp_path)

    status = control.read_status(tmp_path)
    assert status["state"] == "task" and status["task"] == {"id": "t2"}
    assert status["chief"] == "http://chief"
    assert status["backends"] == ["claude"]
    assert status["last_task"]["kind"] == "review"


def test_ago_and_last_task_line_render_human_lines():
    assert control.ago(30) == "just now"
    assert control.ago(5 * 60) == "5m ago"
    assert control.ago(3 * 3600) == "3h ago"
    assert control.ago(2 * 24 * 3600) == "2d ago"

    assert control.last_task_line({}) == ""
    line = control.last_task_line(
        {
            "last_task": {
                "kind": "review",
                "repo": "https://github.com/o/rust-td.git",
                "is_error": False,
                "finished_at": 900.0,
            }
        },
        now=900.0 + 18 * 60,
    )
    assert line == "review on rust-td ✓ 18m ago"
    failed = control.last_task_line(
        {"last_task": {"kind": "resolve", "repo": "", "is_error": True, "finished_at": 0.0}},
        now=30.0,
    )
    assert failed == "resolve ✗ just now"


def test_missing_or_corrupt_status_reads_empty(tmp_path):
    assert control.read_status(tmp_path) == {}
    control.status_path(tmp_path).write_text("{not json")
    assert control.read_status(tmp_path) == {}
    control.status_path(tmp_path).write_text(json.dumps(["not", "a", "dict"]))
    assert control.read_status(tmp_path) == {}


def test_runner_view_reaches_every_mode(tmp_path):
    # Nothing on disk: no daemon ever ran here.
    assert control.runner_view(tmp_path).mode is RunnerMode.offline

    # This live process wrote "idle".
    control.write_status("idle", state_dir=tmp_path)
    assert control.runner_view(tmp_path).mode is RunnerMode.idle

    # A task in flight: working, and the human line names the repo + elapsed.
    control.write_status(
        "task", task={"kind": "issue", "repo": "https://github.com/o/widget.git"}, state_dir=tmp_path
    )
    since = control.read_status(tmp_path)["since"]
    view = control.runner_view(tmp_path, now=since + 150)
    assert view.mode is RunnerMode.working
    assert "widget" in view.detail and "(2m)" in view.detail

    # Pause requested while that task runs: draining, not yet paused.
    control.request_pause(tmp_path)
    assert control.runner_view(tmp_path).mode is RunnerMode.draining

    # The daemon exited (dead pid) with the flag still set: paused.
    status = control.read_status(tmp_path)
    status["pid"] = _dead_pid()
    control.status_path(tmp_path).write_text(json.dumps(status))
    assert control.runner_view(tmp_path).mode is RunnerMode.paused

    # Flag removed but no live daemon: offline (launchd hasn't respawned yet).
    control.clear_pause(tmp_path)
    assert control.runner_view(tmp_path).mode is RunnerMode.offline

    modes_seen = {RunnerMode.offline, RunnerMode.idle, RunnerMode.working,
                  RunnerMode.draining, RunnerMode.paused}
    assert modes_seen == set(RunnerMode)


def test_menubar_renders_every_mode():
    pytest.importorskip("rumps")  # macOS-only extra; skipped on the Linux fleet
    from hive.runner import menubar

    assert set(menubar.TITLES) == set(RunnerMode)
    assert set(menubar.TOGGLE_LABELS) == set(RunnerMode)


def test_daemon_main_exits_at_startup_when_paused(tmp_path, monkeypatch):
    """A paused machine that reboots (RunAtLoad) must come up, notice the
    flag, and stay down — without registering anywhere."""
    monkeypatch.setenv("HIVE_RUNNER_STATE_DIR", str(tmp_path))
    from hive.runner import _daemon

    monkeypatch.setattr(
        _daemon,
        "WorkerLoop",
        lambda *a, **k: pytest.fail("paused daemon must not build the worker loop"),
    )
    control.request_pause(tmp_path)

    _daemon.main([])

    assert control.read_status(tmp_path)["state"] == "paused"
