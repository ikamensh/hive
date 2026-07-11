"""Menu bar switch for the Mac runner — the Wi-Fi-toggle experience.

A status item (🐝) next to the clock renders `control.runner_view()` and flips
the pause flag. Process lifecycle stays with launchd: the runner LaunchAgent's
KeepAlive is conditioned on the flag being absent, so pausing means "the
daemon drains and launchd leaves it down", resuming means "remove the flag and
kick launchd". The menu app itself never supervises the daemon.

Besides the switch, the menu answers the three glance questions about this
machine's runner: which chief it reports to (and as whom), which agent CLIs
discovery found, and what it finished most recently — all read from the local
status file, no chief round-trip.

Run standalone with `uv run python -m hive.runner.menubar`; installed as the
`com.hive.menubar` LaunchAgent by `deploy/install_mac_runner.sh` (KeepAlive,
so "quit" would be a lie — the escape hatch is Hide, which boots the agent
out until the next login).
"""

from __future__ import annotations

import os
import socket
import subprocess
import webbrowser
from pathlib import Path

import httpx
import rumps

from hive.runner import control
from hive.runner.control import RunnerMode

RUNNER_LABEL = "com.hive.runner"
MENUBAR_LABEL = "com.hive.menubar"
LOG_PATH = Path.home() / "Library/Logs/hive/runner.log"
ENV_FILE = Path(os.environ.get("HIVE_RUNNER_ENV", "~/.config/hive/runner.env")).expanduser()
RUNNER_NAME = os.environ.get("HIVE_RUNNER_NAME") or socket.gethostname().split(".")[0]
REFRESH_S = 3
FLEET_POLL_TICKS = 5  # chief round-trips every ~15s; local files every tick

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


def _env_value(name: str) -> str:
    """A runner.env value, preferring the ambient environment."""
    if value := os.environ.get(name, ""):
        return value
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name:
                return value.strip()
    return ""


def dashboard_url() -> str:
    """The chief this runner reports to, from the environment or its env file."""
    return _env_value("HIVE_URL").split(",")[0].strip()


def chief_client() -> httpx.Client | None:
    """A client for the chief's web API, authed like the CLI: the Caddy
    perimeter's basic auth from runner.env (inside, this deploy runs
    auth_mode=dev). None when no chief URL is configured."""
    url = dashboard_url()
    if not url:
        return None
    basic = _env_value("HIVE_BASIC_AUTH")
    auth = tuple(basic.split(":", 1)) if ":" in basic else None
    return httpx.Client(base_url=url, auth=auth, timeout=5.0)


def agents_line(status: dict) -> str:
    """Only agents the chief would actually dispatch to count as ready;
    installed-but-unusable CLIs are a parenthetical, not a claim."""
    usable = status.get("usable") or []
    installed = status.get("backends") or []
    if usable:
        return "Agents ready: " + ", ".join(usable)
    if installed:
        return f"Agents ready: none ({len(installed)} installed, not usable)"
    return "Agents ready: none detected"


class HiveMenuBar(rumps.App):
    def __init__(self) -> None:
        # Callback-less items render gray: the standard macOS info-line look.
        self.status_item = rumps.MenuItem("Starting…")
        self.chief_item = rumps.MenuItem("")
        self.agents_item = rumps.MenuItem("")
        self.last_item = rumps.MenuItem("")
        self.toggle_item = rumps.MenuItem("Pause runner", callback=self.on_toggle)
        self.stop_item = rumps.MenuItem("Stop now (kills current task)")
        self.fleet_item = rumps.MenuItem("Pause all of hive")
        self.fleet_paused: bool | None = None  # None = chief not asked/reachable yet
        self._tick = 0
        super().__init__(
            "Hive Runner",
            title=TITLES[RunnerMode.offline],
            quit_button=None,
            menu=[
                self.status_item,
                self.chief_item,
                self.agents_item,
                self.last_item,
                None,
                self.toggle_item,
                self.stop_item,
                None,
                self.fleet_item,
                None,
                rumps.MenuItem("Open dashboard", callback=self.on_dashboard),
                rumps.MenuItem("Show logs", callback=self.on_logs),
                None,
                # Under launchd KeepAlive a plain quit would respawn in
                # seconds and read as "does nothing" — Hide boots the agent
                # out instead; RunAtLoad brings the icon back at next login.
                rumps.MenuItem("Hide menu bar icon (back at login)", callback=self.on_hide),
            ],
        )
        # Not @rumps.timer: that registers the *unbound* method at class-definition
        # time, so the firing callback would be called without `self`.
        rumps.Timer(self.refresh, REFRESH_S).start()
        self.refresh(None)

    def refresh(self, _timer) -> None:
        view = control.runner_view()
        status = control.read_status()
        self.title = TITLES[view.mode]
        self.status_item.title = view.detail
        chief = control.chief_host(status.get("chief") or dashboard_url()) or "—"
        self.chief_item.title = f"Runner {RUNNER_NAME} → {chief}"
        self.agents_item.title = agents_line(status)
        last_line = control.last_task_line(status)
        self.last_item.title = f"Last: {last_line}"
        self.last_item._menuitem.setHidden_(not last_line)
        self.toggle_item.title = TOGGLE_LABELS[view.mode]
        # A gray (callback-less) item can't be clicked; only offer the hard
        # stop while something is actually running.
        can_stop = view.mode in (RunnerMode.working, RunnerMode.draining)
        self.stop_item.set_callback(self.on_stop_now if can_stop else None)
        if self._tick % FLEET_POLL_TICKS == 0:
            self.poll_fleet()
        self._tick += 1
        self.render_fleet_item()

    def poll_fleet(self) -> None:
        client = chief_client()
        if client is None:
            self.fleet_paused = None
            return
        try:
            with client:
                self.fleet_paused = bool(
                    client.get("/api/workspace").raise_for_status().json()["paused"]
                )
        except (httpx.HTTPError, KeyError, ValueError):
            self.fleet_paused = None

    def render_fleet_item(self) -> None:
        if self.fleet_paused is None:
            self.fleet_item.title = "Hive: chief unreachable"
            self.fleet_item.set_callback(None)
        elif self.fleet_paused:
            self.fleet_item.title = "Resume hive (paused — nothing new starts)"
            self.fleet_item.set_callback(self.on_fleet_toggle)
        else:
            self.fleet_item.title = "Pause all of hive (running tasks finish)"
            self.fleet_item.set_callback(self.on_fleet_toggle)

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

    def on_fleet_toggle(self, _item) -> None:
        client = chief_client()
        if client is None or self.fleet_paused is None:
            return
        try:
            with client:
                self.fleet_paused = bool(
                    client.patch("/api/workspace", json={"paused": not self.fleet_paused})
                    .raise_for_status()
                    .json()["paused"]
                )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            rumps.alert("Could not reach the hive chief", str(exc))
        self.render_fleet_item()

    def on_dashboard(self, _item) -> None:
        if url := dashboard_url():
            webbrowser.open(url)

    def on_logs(self, _item) -> None:
        if LOG_PATH.exists():
            subprocess.run(["open", str(LOG_PATH)], timeout=15)

    def on_hide(self, _item) -> None:
        # bootout SIGTERMs this very process; the fallback quit only runs when
        # we're not under launchd (dev invocation from a terminal).
        if _launchctl("bootout", f"gui/{os.getuid()}/{MENUBAR_LABEL}").returncode != 0:
            rumps.quit_application()


def main() -> None:
    HiveMenuBar().run()


if __name__ == "__main__":
    main()
