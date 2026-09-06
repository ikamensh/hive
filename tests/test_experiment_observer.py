"""The standalone observer records real HTTP reads and survives an outage."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/observe_experiment.py"


def test_observer_records_changes_capacity_and_outage_without_mutations(tmp_path):
    """A transient server failure heals in-process; unchanged heartbeat clocks
    do not create snapshots, and task results/validation survive in evidence."""
    requests = []
    project_reads = 0

    class Chief(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            nonlocal project_reads
            requests.append((self.command, self.path))
            if len(requests) == 1:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"provider-secret-token must not be printed")
                return
            if self.path == "/api/projects":
                payload = [{"id": "p1", "name": "simulator"}]
            elif self.path == "/api/projects/p1":
                project_reads += 1
                done = project_reads >= 3
                task = {"id": "t1", "backend": "claude", "model": "claude-fable-5-1",
                        "kind": "review", "status": "done" if done else "running",
                        "created_at": 100.0, "started_at": 105.0, "finished_at": 115.0 if done else 0,
                        "updated_at": project_reads, "dispatch_reason": "Astra probing; selected Fable",
                        "result_text": "REVIEW: ACCEPT" if done else ""}
                if done:
                    task["validation"] = {"command": "make check", "exit_code": 0,
                                          "output": "tests passed", "commit_sha": "a" * 40}
                payload = {"project": {"id": "p1", "name": "simulator", "state": "working"},
                           "state_reason": "complete" if done else "reviewing", "tasks": [task],
                           "plan": {"plan": {"status": "complete" if done else "approved"},
                                    "items": [{"id": "i1", "status": "done" if done else "reviewing",
                                               "repair_attempts": 1}], "tasks": [task]}}
            elif self.path == "/api/resources":
                payload = {"resources": [{"backend": "claude", "model_cooldowns": {"opus": 9999}}]}
            elif self.path == "/api/show":
                payload = {"limits": [{"backend": "claude", "windows": [{"model_scope": "fable"}]}]}
            else:
                raise AssertionError(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_POST(self):
            raise AssertionError("observer must never mutate the chief")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Chief)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "evidence"
    process = subprocess.Popen([sys.executable, str(SCRIPT), "simulator", "--output", str(output),
                                "--url", f"http://127.0.0.1:{server.server_port}", "--interval", "0.02",
                                "--resource-interval", "0.03"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 8
        report_path = output / "report.md"
        while time.monotonic() < deadline:
            if report_path.exists() and "Landed: 1 / 1" in report_path.read_text():
                break
            assert process.poll() is None, process.communicate()
            time.sleep(0.02)
        else:
            raise AssertionError("observer did not reach completed plan")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0
        assert "provider-secret-token" not in stdout + stderr
        events = [json.loads(line) for line in (output / "timeline.jsonl").read_text().splitlines()]
        assert [event["event"] for event in events].count("outage") == 1
        assert [event["event"] for event in events].count("recovered") == 1
        changes = [event for event in events if event["event"] == "project_changed"]
        assert len(changes) == 2
        assert changes[0]["tasks"][0]["dispatch_reason"] == "Astra probing; selected Fable"
        final = json.loads((output / changes[-1]["snapshot"]).read_text())["payload"]
        assert final["tasks"][0]["validation"]["commit_sha"] == "a" * 40
        capacity = next(event for event in events if event["event"] == "capacity")
        assert json.loads((output / capacity["snapshot"]).read_text())["payload"]["limits"][0]["windows"][0]["model_scope"] == "fable"
        report = report_path.read_text()
        assert "claude / claude-fable-5-1 | 1 | 1 | 1 | 0 | 10.0 | 5.0" in report
        assert "Repair attempts: 1" in report and "Observer outages: 1" in report and "Stopped:" in report
        assert all(method == "GET" for method, _ in requests)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
