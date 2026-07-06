"""Environment-driven configuration for the chief."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from hive.runner._machine import machine_metadata
from hive.models import DEFAULT_WORKSPACE_ID


@dataclass
class Config:
    gcp_project: str  # empty = FileStore under data_dir; set for Firestore
    gcs_bucket: str  # empty = local blob store under data_dir
    gh_token: str
    gemini_api_key: str
    orch_model: str
    runner_token: str
    data_dir: Path
    orch_provider: str = "auto"  # auto | openai | gemini
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    issue_backend: str = "codex"
    issue_model: str = ""  # empty = let the backend/CLI use its current configured default
    github_webhook_secret: str = ""  # HMAC secret for the CI webhook; empty = webhook disabled
    test_refresh_backend: str = "codex"
    test_refresh_model: str = ""
    test_sweep_backend: str = "codex"
    test_sweep_model: str = ""
    test_confirm_backend: str = "codex"
    test_confirm_model: str = ""
    auth_mode: str = "dev"  # dev | github
    # Order matters: the first login is the dev-mode identity.
    allowed_github_users: str = "ikamensh,eidemiurge"
    github_client_id: str = ""
    github_client_secret: str = ""
    public_url: str = "http://localhost:8000"
    # URLs this chief tells runners it is reachable on (register response);
    # runners persist them as reconnect candidates. Empty = public_url.
    advertised_urls: str = ""
    auth_secret: str = ""
    workspace_id: str = DEFAULT_WORKSPACE_ID
    workspace_name: str = "ikamen"
    machine_id: str = ""
    machine_name: str = ""
    machine_type: str = ""
    machine_os: str = ""
    machine_arch: str = ""
    machine_kind: str = ""
    autostart_runner: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("HIVE_DATA_DIR", "/tmp/hive-data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        machine = machine_metadata()
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
            issue_backend=os.environ.get("HIVE_ISSUE_BACKEND", "codex"),
            issue_model=os.environ.get("HIVE_ISSUE_MODEL", ""),
            github_webhook_secret=os.environ.get("HIVE_GITHUB_WEBHOOK_SECRET", ""),
            test_refresh_backend=os.environ.get("HIVE_TEST_REFRESH_BACKEND", "codex"),
            test_refresh_model=os.environ.get("HIVE_TEST_REFRESH_MODEL", ""),
            test_sweep_backend=os.environ.get("HIVE_TEST_SWEEP_BACKEND", "codex"),
            test_sweep_model=os.environ.get("HIVE_TEST_SWEEP_MODEL", ""),
            test_confirm_backend=os.environ.get("HIVE_TEST_CONFIRM_BACKEND", "codex"),
            test_confirm_model=os.environ.get("HIVE_TEST_CONFIRM_MODEL", ""),
            auth_mode=os.environ.get("HIVE_AUTH_MODE", "dev"),
            allowed_github_users=os.environ.get(
                "HIVE_ALLOWED_GITHUB_USERS", "ikamensh,eidemiurge"
            ),
            github_client_id=os.environ.get("HIVE_GITHUB_CLIENT_ID", ""),
            github_client_secret=os.environ.get("HIVE_GITHUB_CLIENT_SECRET", ""),
            public_url=os.environ.get("HIVE_PUBLIC_URL", "http://localhost:8000"),
            advertised_urls=os.environ.get("HIVE_ADVERTISED_URLS", ""),
            auth_secret=os.environ.get("HIVE_AUTH_SECRET", ""),
            workspace_id=os.environ.get("HIVE_WORKSPACE_ID", DEFAULT_WORKSPACE_ID),
            workspace_name=os.environ.get("HIVE_WORKSPACE_NAME", "ikamen"),
            machine_id=os.environ.get("HIVE_MACHINE_ID", ""),
            machine_name=os.environ.get("HIVE_MACHINE_NAME", socket.gethostname()),
            machine_type=machine["machine_type"],
            machine_os=machine["machine_os"],
            machine_arch=machine["machine_arch"],
            machine_kind=machine["machine_kind"],
            autostart_runner=os.environ.get("HIVE_AUTOSTART_RUNNER", "").lower()
            in {"1", "true", "yes", "on"},
        )
