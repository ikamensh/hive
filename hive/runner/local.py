"""Local runner process management for single-machine Hive installs."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

from hive.config.settings import Config
from hive.config.file import set_stored_config_value


TRUE_VALUES = {"1", "true", "yes", "on"}


def truthy(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in TRUE_VALUES


def autostart_enabled(env: dict[str, str] | None = None) -> bool:
    env = env or os.environ
    return truthy(env.get("HIVE_AUTOSTART_RUNNER"))


def local_control_plane_url(host: str, port: int) -> str:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{connect_host}:{port}"


class LocalRunnerManager:
    """Starts a runner daemon on the same host as the control plane.

    This is intentionally explicit: the browser cannot start a process on the
    user's laptop. In local setups the control-plane host and browser machine
    are usually the same, so this gives the Resources page a safe "enroll this
    host" action.
    """

    def __init__(self, config: Config):
        self.config = config
        self.runner_name = os.environ.get(
            "HIVE_RUNNER_NAME",
            config.machine_name or socket.gethostname(),
        )
        self._proc: subprocess.Popen | None = None
        self._log = None

    def status(self, *, message: str = "") -> dict:
        running = self._proc is not None and self._proc.poll() is None
        return {
            "supported": True,
            "running": running,
            "registered": False,
            "runner_name": self.runner_name,
            "pid": self._proc.pid if running and self._proc else 0,
            "autostart": self.config.autostart_runner,
            "log_path": str(self.log_path()),
            "message": message,
        }

    def set_autostart(self, enabled: bool) -> dict:
        self.config.autostart_runner = enabled
        set_stored_config_value("HIVE_AUTOSTART_RUNNER", "true" if enabled else "false")
        return self.status(message="local runner autostart updated")

    def log_path(self) -> Path:
        return self.config.data_dir / "local-runner.log"

    def start(self) -> dict:
        if self._proc is not None and self._proc.poll() is None:
            return self.status(message="local runner already running")

        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(
            {
                "HIVE_URL": self.config.public_url,
                "HIVE_RUNNER_TOKEN": self.config.runner_token,
                "HIVE_WORKSPACE_ID": self.config.workspace_id,
                "HIVE_MACHINE_ID": self.config.machine_id,
                "HIVE_MACHINE_NAME": self.config.machine_name or self.runner_name,
                "HIVE_MACHINE_TYPE": self.config.machine_type,
                "HIVE_MACHINE_OS": self.config.machine_os,
                "HIVE_MACHINE_ARCH": self.config.machine_arch,
                "HIVE_MACHINE_KIND": self.config.machine_kind,
                "HIVE_RUNNER_NAME": self.runner_name,
                "HIVE_RUNNER_WORKDIR": env.get(
                    "HIVE_RUNNER_WORKDIR",
                    str(self.config.data_dir / "runner-work"),
                ),
            }
        )
        self._log = self.log_path().open("ab")
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "hive.runner.daemon"],
                env=env,
                stdout=self._log,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            self._log.close()
            self._log = None
            raise
        return self.status(message="local runner starting")

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
        if self._log is not None:
            self._log.close()
            self._log = None
