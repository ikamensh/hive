"""Switch a Mac's capacity between its enrolled chief and a local chief.

launchd owns service lifetimes. A switch drains the old worker before starting
another, and the persisted selection also drives the CLI and menu bar.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from hive.config import desktop
from hive.config.file import config_path
from hive.runner import control

CHIEF_LABEL = "com.hive.local-chief"
RUNNER_LABEL = "com.hive.runner"
LOG_DIR = Path.home() / "Library/Logs/hive"


def client(mode: str | None = None) -> httpx.Client:
    url, auth, token = desktop.target(mode)
    return httpx.Client(
        base_url=url,
        auth=auth,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=5,
    )


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, timeout=15, check=check
    )


def job(label: str) -> str:
    return f"gui/{os.getuid()}/{label}"


def start_job(label: str) -> None:
    if launchctl("print", job(label), check=False).returncode:
        launchctl(
            "bootstrap",
            f"gui/{os.getuid()}",
            str(Path.home() / f"Library/LaunchAgents/{label}.plist"),
        )
    launchctl("kickstart", job(label))


class LaunchdServices:
    def prepare(self, mode: str) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Desktop chief switching currently requires macOS.")
        if mode == "remote":
            desktop.target("remote")  # fail before touching the active worker
            path = Path.home() / f"Library/LaunchAgents/{RUNNER_LABEL}.plist"
            if not path.exists():
                raise FileNotFoundError(f"Remote runner is not installed: {path}")
            return
        from hive.cli import _prepare_web_bundle

        _prepare_web_bundle(skip_build=False)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = Path.home() / f"Library/LaunchAgents/{CHIEF_LABEL}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            plistlib.dumps(
                {
                    "Label": CHIEF_LABEL,
                    "ProgramArguments": [sys.executable, "-m", "hive.runner.desktop", "serve"],
                    "WorkingDirectory": str(Path(__file__).resolve().parents[2]),
                    "EnvironmentVariables": {
                        "PATH": os.environ["PATH"],
                        "HIVE_CONFIG_FILE": str(config_path()),
                    },
                    "RunAtLoad": True,
                    # In remote mode, serve exits successfully at login. Crashes in
                    # local mode restart; deliberate stops are handled via bootout.
                    "KeepAlive": {"SuccessfulExit": False},
                    "ThrottleInterval": 10,
                    "StandardOutPath": str(LOG_DIR / "local-chief.log"),
                    "StandardErrorPath": str(LOG_DIR / "local-chief.log"),
                }
            )
        )

    def start(self, mode: str) -> None:
        if mode == "remote":
            start_job(RUNNER_LABEL)
            return
        start_job(CHIEF_LABEL)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with client("local") as api:
                    data = api.get("/api/auth/me").raise_for_status().json()
                    expected = str(desktop.local_data() / "store")
                    if data["storage"]["store_path"] != expected:
                        raise RuntimeError(
                            f"Port 8787 belongs to another chief (expected {expected})."
                        )
                    # Also resumes a local worker paused through the menu.
                    api.post("/api/local-runner/start").raise_for_status()
                    return
            except httpx.TransportError:
                time.sleep(0.25)
        raise TimeoutError(f"Local chief did not start. See {LOG_DIR / 'local-chief.log'}")

    def stop(self, mode: str) -> None:
        if mode == "local" and launchctl("print", job(CHIEF_LABEL), check=False).returncode == 0:
            launchctl("bootout", job(CHIEF_LABEL))


def switch(mode: str, *, services=None, timeout: float = 3600) -> dict:
    if mode not in desktop.MODES:
        raise ValueError(f"Unknown chief: {mode}")
    desktop.directory().mkdir(parents=True, exist_ok=True)
    with (desktop.directory() / "switch.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A chief switch is already in progress.") from None
        services = services or LaunchdServices()
        services.prepare(mode)
        old = desktop.selected() or "remote"
        if old != mode:
            state = desktop.runner_state(old)
            print(f"Draining the {old} runner; its current task will finish first…", flush=True)
            control.request_pause(state)
            deadline = time.monotonic() + timeout
            while True:
                worker = control.read_status(state)
                # The daemon writes paused only after its task has reported.
                # A local child can remain a zombie until the chief reaps it;
                # pid existence alone would wait forever in that case.
                if worker.get("state") == "paused" or not control.pid_alive(worker.get("pid")):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{old} runner is still draining; retry hive switch {mode}.")
                time.sleep(0.1)
            services.stop(old)
        # A startup failure leaves the old worker paused and the selected
        # chief visibly offline. Retrying or switching back is explicit.
        desktop.select(mode)
        control.clear_pause(desktop.runner_state(mode))
        services.start(mode)
        deadline = time.monotonic() + 60
        while True:
            worker = control.read_status(desktop.runner_state(mode))
            if worker.get("state") != "paused" and control.pid_alive(worker.get("pid")):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{mode} runner did not start; check its log and retry.")
            time.sleep(0.1)
        result = status()
        print(f"Using {mode} chief: {result['url']}", flush=True)
        return result


def status() -> dict:
    mode = desktop.selected() or "remote"
    return {
        "mode": mode,
        "url": desktop.target(mode)[0],
        "runner": control.runner_view(desktop.runner_state(mode)).detail,
        "data_dir": str(desktop.local_data()) if mode == "local" else None,
    }


def resume_runner() -> None:
    switch(desktop.selected() or "remote")


def switching() -> bool:
    path = desktop.directory() / "switch.lock"
    if not path.exists():
        return False
    with path.open() as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def terminate_runner() -> None:
    mode = desktop.selected() or "remote"
    if mode == "remote":
        launchctl("kill", "SIGTERM", job(RUNNER_LABEL))
    else:
        pid = control.read_status(desktop.runner_state(mode)).get("pid")
        if control.pid_alive(pid):
            os.kill(pid, signal.SIGTERM)


def serve() -> None:
    if desktop.selected() != "local":
        return
    from hive.cli import _run_chief

    # Reuse the installed GitHub access; remote client credentials and data
    # settings never become this local chief's target or storage.
    if token := desktop.remote_env().get("HIVE_GH_TOKEN"):
        os.environ.setdefault("HIVE_GH_TOKEN", token)
    _run_chief(
        argparse.Namespace(
            local=True,
            data_dir=str(desktop.local_data()),
            host="127.0.0.1",
            port=8787,
            reload=False,
            no_web_build=True,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", *desktop.MODES])
    args = parser.parse_args()
    if args.action == "serve":
        serve()
    else:
        switch(args.action)
