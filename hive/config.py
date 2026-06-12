"""Environment-driven configuration for the control plane."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    gcp_project: str  # empty = in-memory store (dev/tests)
    gcs_bucket: str  # empty = local blob store under data_dir
    gh_token: str
    gemini_api_key: str
    orch_model: str
    runner_token: str
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("HIVE_DATA_DIR", "/tmp/hive-data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            gcp_project=os.environ.get("HIVE_GCP_PROJECT", ""),
            gcs_bucket=os.environ.get("HIVE_GCS_BUCKET", ""),
            gh_token=os.environ.get("HIVE_GH_TOKEN", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            orch_model=os.environ.get("HIVE_ORCH_MODEL", "gemini-3.5-flash"),
            runner_token=os.environ.get("HIVE_RUNNER_TOKEN", "dev-token"),
            data_dir=data_dir,
        )
