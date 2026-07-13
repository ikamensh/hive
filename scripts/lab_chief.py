"""Isolated lab chief for live testing: production wiring, file-backed state.

Managed-state-only (wiki/managed-state-only.md) makes `production_app` refuse
FileStore, and pointing a second chief at the real Firestore is fenced out by
the leader lease — so live experiments get this launcher: the same supervisor,
orchestrator, pollers, and runner protocol against a scratch directory.

    export HIVE_DATA_DIR=/tmp/hive-lab HIVE_RUNNER_TOKEN=lab-token
    # + orchestrator creds (HIVE_ORCH_PROVIDER / OPENAI_API_KEY / ...)
    uv run uvicorn --factory --app-dir scripts lab_chief:lab_app --port 8123
    # runner against it:
    HIVE_URL=http://127.0.0.1:8123 HIVE_RUNNER_TOKEN=lab-token \
      HIVE_MACHINE_NAME=lab-mac HIVE_DATA_DIR=/tmp/hive-lab-runner \
      uv run python -m hive.runner

Not a supported runtime mode — for tests and labs only.
"""

import logging
import os
from pathlib import Path

from hive.api import create_app, make_ci_check, make_issue_scan, make_testing_check, make_todo_triage, mount_spa
from hive.config.settings import Config
from hive.persistence.blobstore import LocalBlobStore
from hive.persistence.store import FileStore
from hive._control.orchestrator import Orchestrator
from hive._control.supervisor import Supervisor


def lab_app():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = Config.from_env()
    store = FileStore(Path(config.data_dir) / "store")
    blobs = LocalBlobStore(Path(config.data_dir) / "blobs")
    orchestrator = Orchestrator(store, blobs, config)
    supervisor = Supervisor(
        store,
        orchestrator.invoke,
        workspace_id=config.workspace_id,
        machine_name=os.environ.get("HIVE_MACHINE_NAME", "lab-chief"),
        ci_check=make_ci_check(store, config),
        testing_check=make_testing_check(store, config),
        issue_scan=make_issue_scan(store, config, blobs=blobs),
        todo_triage=make_todo_triage(store, config),
    )
    app = create_app(store, supervisor, config, blobs=blobs)
    mount_spa(app, Path(os.environ.get("HIVE_WEB_DIST", "web/dist")))
    return app
