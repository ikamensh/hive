"""The chief selected for this desktop; shared by CLI, services and menu bar."""

import os
from pathlib import Path

from hive.config.file import config_path, load_stored_config

LOCAL_URL = "http://127.0.0.1:8787"
MODES = ("local", "remote")


def directory() -> Path:
    return config_path().parent


def selected() -> str | None:
    path = directory() / "selected-chief"
    if not path.exists():
        return None
    mode = path.read_text().strip()
    if mode not in MODES:
        raise ValueError(f"Invalid chief selection in {path}: {mode!r}")
    return mode


def select(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Unknown chief: {mode}")
    directory().mkdir(parents=True, exist_ok=True)
    path = directory() / "selected-chief"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(mode + "\n")
    tmp.replace(path)


def remote_env() -> dict[str, str]:
    path = Path(
        os.environ.get("HIVE_RUNNER_ENV_FILE")
        or os.environ.get("HIVE_RUNNER_ENV")
        or directory() / "runner.env"
    )
    return load_stored_config(path.expanduser())


def local_data() -> Path:
    return Path.home() / ".local/share/hive-local"


def runner_state(mode: str | None = None) -> Path:
    return local_data() / "runner-state" if (mode or selected()) == "local" else directory()


def target(mode: str | None = None) -> tuple[str, tuple[str, str] | None, str]:
    if (mode or selected()) == "local":
        return LOCAL_URL, None, ""
    values = remote_env()
    url = values.get("HIVE_URL", "").split(",")[0].strip()
    if not url:
        raise ValueError("No remote chief configured; install/enroll this Mac's runner first.")
    basic = values.get("HIVE_BASIC_AUTH", "")
    auth = tuple(basic.split(":", 1)) if basic else None
    return url, auth, values.get("HIVE_TOKEN", "")
