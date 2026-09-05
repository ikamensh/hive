"""Real chief/runner processes: local startup, persistence, and shutdown without providers."""

import os
import signal
import socket
import subprocess
import sys
import time

import httpx


def test_local_chief_starts_runner_and_reopens_state(tmp_path):
    """One command owns both processes; restarting reopens the same projects and runner."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HIVE_", "OPENAI_", "GEMINI_"))}
    # No coding CLIs: this smoke tests lifecycle without spending real quota.
    env.update(PATH="/usr/bin:/bin", HIVE_CONFIG_FILE=str(tmp_path / "config.env"),
               HIVE_GH_TOKEN="unused", PYTHONUNBUFFERED="1")
    command = [sys.executable, "-m", "hive.cli", "run", "--local", "--data-dir", str(tmp_path),
               "--port", str(port), "--no-web-build"]
    project_id = runner_id = None
    for attempt in range(2):
        with (tmp_path / f"chief-{attempt}.log").open("w+") as log:
            started = time.time()
            proc = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        assert proc.poll() is None, (log.seek(0), log.read())
                        try:
                            resources = client.get("/api/resources").raise_for_status().json()
                            if (resources["local_runner"]["registered"]
                                    and resources["runners"][0]["last_seen"] >= started):
                                break
                        except httpx.TransportError:
                            pass
                        assert time.monotonic() < deadline, "local runner failed to register"
                        time.sleep(0.1)
                    current_runner = resources["runners"][0]["id"]
                    runner_pid = resources["local_runner"]["pid"]
                    if attempt == 0:
                        project_id = client.post("/api/projects", json={"name": "durable"}).json()["id"]
                        runner_id = current_runner
                    else:
                        assert current_runner == runner_id
                        assert [p["id"] for p in client.get("/api/projects").json()] == [project_id]
                    assert (tmp_path / "runner-state" / "chiefs.json").is_file()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise
            assert proc.returncode in (0, -signal.SIGTERM), (log.seek(0), log.read())
            from hive.runner.control import pid_alive
            assert not pid_alive(runner_pid)
