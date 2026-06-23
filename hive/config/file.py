"""Hive's machine-local config file.

This is separate from the runtime ``Config`` object because the CLI and local
control-plane API both need to read/write the durable machine preference file.
"""

from __future__ import annotations

import os
from pathlib import Path


# Keys hive will store for itself and apply when launching the control plane.
# Restricting the set keeps `config set` self-documenting and the file tidy.
CONFIG_KEYS: dict[str, str] = {
    "HIVE_GH_TOKEN": "GitHub token for clone/push (auto-detected from `gh auth token`)",
    "OPENAI_API_KEY": "OpenAI API key for the orchestrator",
    "GEMINI_API_KEY": "Gemini API key for the orchestrator",
    "HIVE_ORCH_PROVIDER": "orchestrator provider: auto | openai | gemini",
    "HIVE_ORCH_MODEL": "pin a specific orchestrator model",
    "HIVE_OPENAI_BASE_URL": "OpenAI-compatible endpoint base URL",
    "HIVE_GCP_PROJECT": "Firestore project for required managed runtime state",
    "HIVE_GCS_BUCKET": "GCS bucket for required managed blob state",
    "HIVE_RUNNER_TOKEN": "shared token runners present as X-Hive-Token",
    "HIVE_URL": "control plane base URL the CLI sends commands to (client target)",
    "HIVE_BASIC_AUTH": "user:pass for a control plane behind basic auth, e.g. Caddy (client)",
    "HIVE_TOKEN": "bearer token for app-level (github) auth when driving a remote (client)",
    "HIVE_AUTOSTART_RUNNER": "true/false: start a local runner with `hive run`",
    "HIVE_AUTH_MODE": "auth mode: dev | github",
    "HIVE_ALLOWED_GITHUB_USERS": "comma-separated GitHub users allowed to log in",
    "HIVE_GITHUB_CLIENT_ID": "GitHub OAuth app client ID",
    "HIVE_GITHUB_CLIENT_SECRET": "GitHub OAuth app client secret",
    "HIVE_AUTH_SECRET": "secret used to sign Hive browser sessions",
    "HIVE_PUBLIC_URL": "public base URL for OAuth callbacks",
    "HIVE_WORKSPACE_ID": "active workspace ID for this control-plane process",
    "HIVE_WORKSPACE_NAME": "display name for the active workspace",
    "HIVE_MACHINE_ID": "stable ID for this machine",
    "HIVE_MACHINE_NAME": "display name for this machine",
}


def config_path() -> Path:
    return Path(os.environ.get("HIVE_CONFIG_FILE", "~/.config/hive/config.env")).expanduser()


def load_stored_config(path=None) -> dict[str, str]:
    """Read hive's own KEY=VALUE token store. Missing file means fresh machine."""
    path = path or config_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def save_stored_config(values: dict[str, str], path=None):
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}={values[k]}\n" for k in sorted(values))
    path.write_text(body)
    path.chmod(0o600)
    return path


def set_stored_config_value(key: str, value: str, path=None) -> Path:
    if key not in CONFIG_KEYS:
        raise KeyError(key)
    values = load_stored_config(path)
    values[key] = value
    return save_stored_config(values, path)
