"""Menu bar switch for the Mac runner — the Wi-Fi-toggle experience.

A status item (🐝) next to the clock renders `control.runner_view()` and flips
the pause flag. Process lifecycle stays with launchd: the runner LaunchAgent's
KeepAlive is conditioned on the flag being absent, so pausing means "the
daemon drains and launchd leaves it down", resuming means "remove the flag and
kick launchd". The menu app itself never supervises the daemon.

Run standalone with `uv run python -m hive.runner.menubar`; installed as the
`com.hive.menubar` LaunchAgent by `deploy/install_mac_runner.sh`.
"""

from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path

import rumps

from hive.runner import control
from hive.runner.control import RunnerMode

RUNNER_LABEL = "com.hive.runner"
LOG_PATH = Path.home() / "Library/Logs/hive/runner.log"
ENV_FILE = Path(os.environ.get("HIVE_RUNNER_ENV", "~/.config/hive/runner.env")).expanduser()
REFRESH_S = 3

TITLES = {
    RunnerMode.idle: "🐝",
    RunnerMode.working: "🐝⚡",
    RunnerMode.draining: "🐝⏳",
    RunnerMode.paused: "🐝💤",
    RunnerMode.offline: "🐝⚠️",
}
TOGGLE_LABELS = {
    RunnerMode.idle: "Pause runner",
    RunnerMode.working: "Pause runner (finish current task)",
    RunnerMode.draining: "Resume runner",
    RunnerMode.paused: "Resume runner",
    RunnerMode.offline: "Start runner",
}


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, timeout=15
    )


def kickstart_runner() -> bool:
    """Ask launchd to start the runner job now (idempotent when running)."""
    return _launchctl("kickstart", f"gui/{os.getuid()}/{RUNNER_LABEL}").returncode == 0


def terminate_runner() -> None:
    """SIGTERM the runner job. With the pause flag set launchd won't respawn
    it; the chief fails any in-flight task once the runner goes silent."""
    _launchctl("kill", "SIGTERM", f"gui/{os.getuid()}/{RUNNER_LABEL}")


def dashboard_url() -> str:
    """The chief this runner reports to, from the environment or its env file."""
    urls = os.environ.get("HIVE_URL", "")
    if not urls and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "HIVE_URL":
                urls = value.strip()
                break
    return urls.split(",")[0].strip()


class HiveMenuBar(rumps.App):
    def __init__(self) -> None:
        self.status_item = rumps.MenuItem("Starting…")  # no callback: gray info line
        self.toggle_item = rumps.MenuItem("Pause runner", callback=self.on_toggle)
        self.stop_item = rumps.MenuItem("Stop now (kills current task)")
        super().__init__(
            "Hive Runner",
            title=TITLES[RunnerMode.offline],
            quit_button=None,
            menu=[
                self.status_item,
                None,
                self.toggle_item,
                self.stop_item,
                None,
                rumps.MenuItem("Open dashboard", callback=self.on_dashboard),
                rumps.MenuItem("Show logs", callback=self.on_logs),
                None,
                rumps.MenuItem("Quit menu (runner keeps its state)", callback=rumps.quit_application),
            ],
        )
        # Not @rumps.timer: that registers the *unbound* method at class-definition
        # time, so the firing callback would be called without `self`.
        rumps.Timer(self.refresh, REFRESH_S).start()
        self.refresh(None)

    def refresh(self, _timer) -> None:
        view = control.runner_view()
        self.title = TITLES[view.mode]
        self.status_item.title = view.detail
        self.toggle_item.title = TOGGLE_LABELS[view.mode]
        # A gray (callback-less) item can't be clicked; only offer the hard
        # stop while something is actually running.
        can_stop = view.mode in (RunnerMode.working, RunnerMode.draining)
        self.stop_item.set_callback(self.on_stop_now if can_stop else None)

    def on_toggle(self, _item) -> None:
        view = control.runner_view()
        if view.mode in (RunnerMode.working, RunnerMode.idle):
            control.request_pause()
        else:  # draining/paused resume; offline restarts
            control.clear_pause()
            if not kickstart_runner():
                # alert, not notification: the latter needs an app bundle id.
                rumps.alert(
                    "Hive runner service is not installed",
                    "Run `bash deploy/install_mac_runner.sh` from the hive repo.",
                )
        self.refresh(None)

    def on_stop_now(self, _item) -> None:
        control.request_pause()
        terminate_runner()
        self.refresh(None)

    def on_dashboard(self, _item) -> None:
        if url := dashboard_url():
            webbrowser.open(url)

    def on_logs(self, _item) -> None:
        if LOG_PATH.exists():
            subprocess.run(["open", str(LOG_PATH)], timeout=15)


def main() -> None:
    HiveMenuBar().run()


if __name__ == "__main__":
    main()
