"""Menu bar switch for the Mac runner — the Wi-Fi-toggle experience.

A status item (🐝) next to the clock renders the selected runner and flips
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
import subprocess
import webbrowser
import threading
from pathlib import Path

import httpx
import rumps

from hive.config import desktop
from hive.runner import control
from hive.runner import desktop as desktop_service
from hive.runner.control import RunnerMode

MENUBAR_LABEL = "com.hive.menubar"
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


def dashboard_url() -> str:
    return desktop.target()[0]


def chief_client() -> httpx.Client:
    return desktop_service.client()


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
        self._mode = desktop.selected()
        self._background: threading.Thread | None = None
        self._error: str | None = None
        self.switch_item = rumps.MenuItem("Switch chief", callback=self.on_switch)
        self.hide_item = rumps.MenuItem("Hide menu bar icon (back at login)", callback=self.on_hide)
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
                self.switch_item,
                None,
                rumps.MenuItem("Open dashboard", callback=self.on_dashboard),
                rumps.MenuItem("Show logs", callback=self.on_logs),
                None,
                # Under launchd KeepAlive a plain quit would respawn in
                # seconds and read as "does nothing" — Hide boots the agent
                # out instead; RunAtLoad brings the icon back at next login.
                self.hide_item,
            ],
        )
        # Not @rumps.timer: that registers the *unbound* method at class-definition
        # time, so the firing callback would be called without `self`.
        rumps.Timer(self.refresh, REFRESH_S).start()
        self.refresh(None)

    def refresh(self, _timer) -> None:
        view = control.runner_view(desktop.runner_state())
        status = control.read_status(desktop.runner_state())
        self.title = TITLES[view.mode]
        busy = desktop_service.switching() or (
            self._background is not None and self._background.is_alive()
        )
        self.status_item.title = "Switching / starting chief…" if busy else view.detail
        chief = control.chief_host(dashboard_url())
        mode = desktop.selected() or "remote"
        self.chief_item.title = f"Chief: {mode} · {chief}"
        self.switch_item.title = (
            "Switch to remote chief" if mode == "local" else "Switch to local chief"
        )
        self.switch_item.set_callback(None if busy else self.on_switch)
        self.toggle_item.set_callback(None if busy else self.on_toggle)
        self.hide_item.set_callback(None if busy else self.on_hide)
        if desktop.selected() != self._mode:
            self._mode = desktop.selected()
            self._tick = 0
            self.fleet_paused = None
        if self._error is not None:
            error, self._error = self._error, None
            rumps.alert("Could not switch/start Hive", error)
        self.agents_item.title = agents_line(status)
        last_line = control.last_task_line(status)
        self.last_item.title = f"Last: {last_line}"
        self.last_item._menuitem.setHidden_(not last_line)
        self.toggle_item.title = TOGGLE_LABELS[view.mode]
        # A gray (callback-less) item can't be clicked; only offer the hard
        # stop while something is actually running.
        can_stop = not busy and view.mode in (RunnerMode.working, RunnerMode.draining)
        self.stop_item.set_callback(self.on_stop_now if can_stop else None)
        if self._tick % FLEET_POLL_TICKS == 0:
            self.poll_fleet()
        self._tick += 1
        self.render_fleet_item()
        if busy:
            self.fleet_item.set_callback(None)

    def poll_fleet(self) -> None:
        client = chief_client()
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
        view = control.runner_view(desktop.runner_state())
        if view.mode in (RunnerMode.working, RunnerMode.idle):
            control.request_pause(desktop.runner_state())
        else:  # draining/paused resume; offline restarts
            self.run_background(desktop_service.resume_runner)
        self.refresh(None)

    def on_stop_now(self, _item) -> None:
        control.request_pause(desktop.runner_state())
        desktop_service.terminate_runner()
        self.refresh(None)

    def on_fleet_toggle(self, _item) -> None:
        client = chief_client()
        if self.fleet_paused is None:
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
        log_path = (
            desktop.local_data() / "local-runner.log"
            if desktop.selected() == "local"
            else Path.home() / "Library/Logs/hive/runner.log"
        )
        if log_path.exists():
            subprocess.run(["open", str(log_path)], timeout=15)

    def run_background(self, action) -> None:
        def run():
            try:
                action()
            except (
                OSError,
                RuntimeError,
                ValueError,
                subprocess.SubprocessError,
                httpx.HTTPError,
            ) as exc:
                self._error = str(exc)

        self._background = threading.Thread(target=run, daemon=True)
        self._background.start()

    def on_switch(self, _item) -> None:
        mode = "remote" if desktop.selected() == "local" else "local"
        self.run_background(lambda: desktop_service.switch(mode))
        self.refresh(None)

    def on_hide(self, _item) -> None:
        # bootout SIGTERMs this very process; the fallback quit only runs when
        # we're not under launchd (dev invocation from a terminal).
        if (
            desktop_service.launchctl(
                "bootout", f"gui/{os.getuid()}/{MENUBAR_LABEL}", check=False
            ).returncode
            != 0
        ):
            rumps.quit_application()


def main() -> None:
    HiveMenuBar().run()


if __name__ == "__main__":
    main()
