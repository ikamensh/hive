"""Environment-driven configuration for the control plane."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from hive.models import DEFAULT_WORKSPACE_ID


@dataclass
class Config:
    gcp_project: str  # empty = in-memory store (dev/tests)
    gcs_bucket: str  # empty = local blob store under data_dir
    gh_token: str
    gemini_api_key: str
    orch_model: str
    runner_token: str
    data_dir: Path
    orch_provider: str = "auto"  # auto | openai | gemini
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    auth_mode: str = "dev"  # dev | github
    allowed_github_users: str = "ikamensh"
    github_client_id: str = ""
    github_client_secret: str = ""
    public_url: str = "http://localhost:8000"
    auth_secret: str = ""
    workspace_id: str = DEFAULT_WORKSPACE_ID
    workspace_name: str = "ikamen"
    machine_id: str = ""
    machine_name: str = ""
    autostart_runner: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("HIVE_DATA_DIR", "/tmp/hive-data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            gcp_project=os.environ.get("HIVE_GCP_PROJECT", ""),
            gcs_bucket=os.environ.get("HIVE_GCS_BUCKET", ""),
            gh_token=os.environ.get("HIVE_GH_TOKEN", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            orch_model=os.environ.get("HIVE_ORCH_MODEL", ""),
            runner_token=os.environ.get("HIVE_RUNNER_TOKEN", "dev-token"),
            data_dir=data_dir,
            orch_provider=os.environ.get("HIVE_ORCH_PROVIDER", "auto"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get(
                "HIVE_OPENAI_BASE_URL",
                os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            ),
            auth_mode=os.environ.get("HIVE_AUTH_MODE", "dev"),
            allowed_github_users=os.environ.get("HIVE_ALLOWED_GITHUB_USERS", "ikamensh"),
            github_client_id=os.environ.get("HIVE_GITHUB_CLIENT_ID", ""),
            github_client_secret=os.environ.get("HIVE_GITHUB_CLIENT_SECRET", ""),
            public_url=os.environ.get("HIVE_PUBLIC_URL", "http://localhost:8000"),
            auth_secret=os.environ.get("HIVE_AUTH_SECRET", ""),
            workspace_id=os.environ.get("HIVE_WORKSPACE_ID", DEFAULT_WORKSPACE_ID),
            workspace_name=os.environ.get("HIVE_WORKSPACE_NAME", "ikamen"),
            machine_id=os.environ.get("HIVE_MACHINE_ID", ""),
            machine_name=os.environ.get("HIVE_MACHINE_NAME", socket.gethostname()),
            autostart_runner=os.environ.get("HIVE_AUTOSTART_RUNNER", "").lower()
            in {"1", "true", "yes", "on"},
        )
