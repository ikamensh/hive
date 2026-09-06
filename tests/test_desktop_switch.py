"""Desktop selection routes operator tools to exactly one chief."""

from hive.cli import resolve_targets
from hive.config import desktop


def test_local_selection_overrides_saved_remote_without_sending_its_credentials(
    tmp_path, monkeypatch
):
    """Selecting local preserves remote credentials while keeping them off localhost."""
    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))
    remote = {"HIVE_URL": "https://remote.example", "HIVE_BASIC_AUTH": "user:secret"}
    desktop.select("local")
    (target,) = resolve_targets({}, remote)
    assert target.base_url == desktop.LOCAL_URL
    assert target.auth is None and target.token == ""
    assert remote["HIVE_BASIC_AUTH"] == "user:secret"

    # Explicit one-off URLs still mean exactly what the operator requested.
    (override,) = resolve_targets({"HIVE_URL": "http://explicit"}, {})
    assert override.base_url == "http://explicit"

    (tmp_path / "runner.env").write_text(
        "HIVE_URL=https://remote.example\nHIVE_BASIC_AUTH=user:secret\n"
    )
    desktop.select("remote")
    (target,) = resolve_targets({}, {})
    assert target.base_url == "https://remote.example"
    assert target.auth == ("user", "secret")


def test_switch_drains_old_worker_and_keeps_only_selected_worker_running(tmp_path, monkeypatch):
    """Real worker processes finish their task before the other chief gets capacity."""
    import json
    import subprocess
    import sys
    import time

    from hive.runner import control
    from hive.runner.desktop import switch

    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))
    monkeypatch.setattr(desktop, "local_data", lambda: tmp_path / "local")
    (tmp_path / "runner.env").write_text("HIVE_URL=https://remote.example\n")
    processes = {}
    worker = """
import sys,time
from pathlib import Path
from hive.runner import control
state=Path(sys.argv[1])
control.write_status('task', task={'kind':'test'}, state_dir=state)
time.sleep(.3)
control.write_status('idle', state_dir=state)
while not control.is_paused(state): time.sleep(.01)
control.write_status('paused', state_dir=state)
"""

    class Services:
        def prepare(self, mode):
            pass

        def start(self, mode):
            for other, proc in processes.items():
                if other != mode:
                    assert proc.poll() is not None, "Two chiefs would receive this machine"
            if mode not in processes or processes[mode].poll() is not None:
                processes[mode] = subprocess.Popen(
                    [sys.executable, "-c", worker, str(desktop.runner_state(mode))]
                )
                deadline = time.monotonic() + 5
                while (
                    control.read_status(desktop.runner_state(mode)).get("pid")
                    != processes[mode].pid
                ):
                    assert time.monotonic() < deadline
                    time.sleep(0.01)

        def stop(self, mode):
            # Reap the subprocess, like launchd does for a real service.
            processes[mode].wait(timeout=5)

    services = Services()
    try:
        services.start("remote")
        switch("local", services=services, timeout=5)
        assert desktop.selected() == "local"
        assert control.is_paused(desktop.runner_state("remote"))
        assert processes["local"].poll() is None
        assert json.loads(control.status_path(tmp_path).read_text())["state"] == "paused"
        switch("remote", services=services, timeout=5)
        assert desktop.selected() == "remote"
        assert control.is_paused(desktop.runner_state("local"))
        assert processes["remote"].poll() is None
    finally:
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)


def test_drain_timeout_never_starts_the_other_chief(tmp_path, monkeypatch):
    """A long task leaves the old selection draining; no second worker starts."""
    import pytest
    from hive.runner import control
    from hive.runner.desktop import switch

    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))
    desktop.select("remote")
    control.write_status("task", state_dir=tmp_path)

    class Services:
        def prepare(self, mode):
            pass

        def start(self, mode):
            pytest.fail("Must not start another worker while a task is in flight")

        def stop(self, mode):
            pytest.fail("Must not kill the current task")

    with pytest.raises(TimeoutError, match="still draining"):
        switch("local", services=Services(), timeout=0)
    assert desktop.selected() == "remote"
    assert control.runner_view(tmp_path).mode == control.RunnerMode.draining


def test_menu_bar_and_cli_share_selection(tmp_path, monkeypatch):
    """The dashboard and displayed status directory change with the CLI target."""
    import pytest

    pytest.importorskip("rumps")
    from hive.runner.menubar import dashboard_url, chief_client

    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))
    (tmp_path / "runner.env").write_text(
        "HIVE_URL=https://remote.example\nHIVE_BASIC_AUTH=user:secret\n"
    )
    for mode, expected in [("local", desktop.LOCAL_URL), ("remote", "https://remote.example")]:
        desktop.select(mode)
        assert dashboard_url() == expected
        assert resolve_targets({}, {})[0].base_url == expected
        with chief_client() as api:
            request = api.build_request("GET", "/api/workspace")
            if mode == "local":
                assert "authorization" not in request.headers
                assert desktop.runner_state() == desktop.local_data() / "runner-state"
            else:
                assert desktop.runner_state() == tmp_path


def test_local_start_checks_real_identity_endpoint(tmp_path, monkeypatch):
    """Readiness must use Hive's identity API, not the SPA's catch-all HTML route."""
    from fastapi.testclient import TestClient
    from hive.api import create_app
    from hive.config.settings import Config
    from hive.persistence import FileStore
    from hive.runner import desktop as lifecycle

    monkeypatch.setenv("HIVE_CONFIG_FILE", str(tmp_path / "config.env"))
    monkeypatch.setattr(desktop, "local_data", lambda: tmp_path)
    config = Config(
        storage_mode="local",
        data_dir=tmp_path,
        gcp_project="",
        gcs_bucket="",
        gh_token="",
        gemini_api_key="",
        orch_model="",
        runner_token="test",
    )

    class LocalRunner:
        runner_name = "test"

        def status(self, **kwargs):
            return {"running": True, "pid": 1, "autostart": True, "runner_name": "test"}

        def start(self):
            return self.status()

        def stop(self):
            pass

    from hive._control.supervisor import Supervisor

    store = FileStore(tmp_path / "store")
    app = create_app(
        store, Supervisor(store, lambda *args: None), config, local_runner=LocalRunner()
    )
    monkeypatch.setattr(lifecycle, "start_job", lambda label: None)
    monkeypatch.setattr(lifecycle, "client", lambda mode: TestClient(app))
    lifecycle.LaunchdServices().start("local")
