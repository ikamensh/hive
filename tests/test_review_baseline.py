"""Review scope comes from the remote default commit, never stale local branches."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hive.runner import _daemon as runner


def git(path, *args):
    return subprocess.check_output(["git", *args], cwd=path, text=True).strip()


def commit(path, message):
    git(path, "add", ".")
    git(path, "-c", "user.email=test@example.invalid", "-c", "user.name=Test", "commit", "-m", message)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def review_checkout(tmp_path, monkeypatch):
    """Remote default advances twice while the owned branch retains partial work."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "trunk")
    (seed / "README.md").write_text("Initial docs\n")
    initial = commit(seed, "initial docs")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-u", "origin", "trunk")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/trunk")
    monkeypatch.setattr(runner, "WORKDIR", tmp_path / "work")
    path = runner.checkout(str(origin), "hive/item", fresh_branch=True)
    git(path, "branch", "main", initial)  # the misleading local name from the incident
    (seed / "engine.py").write_text("ENGINE = True\n")
    commit(seed, "land engine")
    git(seed, "push")
    path = runner.checkout(str(origin), "hive/item")
    (path / "dashboard.py").write_text("DASHBOARD = True\n")
    head = commit(path, "build dashboard")
    git(path, "push", "origin", "hive/item")
    (seed / "other.txt").write_text("An independent upstream change\n")
    remote_head = commit(seed, "advance default")
    git(seed, "push")
    (path / "partial.txt").write_text("staged\n")
    git(path, "add", "partial.txt")
    (path / "partial.txt").write_text("unstaged\n")
    (path / "untracked.txt").write_text("keep me\n")
    return path, origin, initial, head, remote_head


@pytest.fixture
def scripted_agent(tmp_path, monkeypatch):
    """The real runner and Codex adapter talk to an executable CLI and HTTP chief."""
    uploaded = {}

    class Chief(BaseHTTPRequestHandler):
        def do_POST(self):
            uploaded[self.path] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"cancel_requested": false}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Chief)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(runner, "HIVE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(runner, "collect_usage", lambda backend: None)  # external account telemetry
    monkeypatch.setenv("KODO_RUNS_DIR", str(tmp_path / "agent-runs"))
    capture = tmp_path / "prompt.txt"
    monkeypatch.setenv("BASELINE_TEST_CAPTURE", str(capture))
    cli = tmp_path / "codex"
    cli.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
os.chdir(sys.argv[sys.argv.index("--cd") + 1])
Path(os.environ["BASELINE_TEST_CAPTURE"]).write_text(sys.argv[2])
scratch = Path(".hive")
scratch.mkdir(exist_ok=True)
(scratch / "result.json").write_text(json.dumps({"task_id":os.environ["BASELINE_TEST_TASK"], "outcome":os.environ["BASELINE_TEST_OUTCOME"]}))
print(json.dumps({"type":"thread.started", "thread_id":"scripted-session"}))
print(json.dumps({"type":"item.completed", "item":{"type":"agent_message", "text":"Completed checks"}}))
''')
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    try:
        yield uploaded, capture
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_preserved_review_receives_frozen_remote_default_baseline(review_checkout, scripted_agent, monkeypatch):
    """Custom remote default and fresh SHA reach the actual agent; fetching and
    provenance capture preserve HEAD, local branches, index and partial files."""
    path, origin, initial, head, remote_head = review_checkout
    uploaded, capture = scripted_agent
    before = {name: git(path, *args) for name, args in {
        "head": ["rev-parse", "HEAD"], "index": ["diff", "--cached"],
        "dirty": ["diff"], "status": ["status", "--porcelain"],
    }.items()}
    monkeypatch.setenv("BASELINE_TEST_TASK", "review-1")
    monkeypatch.setenv("BASELINE_TEST_OUTCOME", "accept")
    stored_instructions = "Review the dashboard. Start by reading the diff against the default branch (`git log` / `git diff`)."
    result = runner.execute({"id": "review-1", "kind": "review", "backend": "codex", "model": "gpt-6-astra",
                             "repo": str(origin), "branch": "hive/item", "preserve_checkout": True,
                             "instructions": stored_instructions}, {}, None)
    assert not result["is_error"] and not result["structured_result_error"]
    baseline = json.loads(uploaded["/api/tasks/review-1/artifacts/review-baseline.json"])
    assert baseline["task_id"] == "review-1" and baseline["base_sha"] == remote_head and baseline["head_sha"] == head
    prompt = capture.read_text()
    assert f"git diff {remote_head}...HEAD" in prompt and f"git log {remote_head}..HEAD" in prompt
    assert prompt.index("Review baseline:") < prompt.index(stored_instructions)
    assert git(path, "diff", "--name-only", f"{remote_head}...HEAD") == "dashboard.py"
    assert git(path, "rev-parse", "main") == git(path, "rev-parse", "trunk") == initial
    assert {name: git(path, *args) for name, args in {
        "head": ["rev-parse", "HEAD"], "index": ["diff", "--cached"],
        "dirty": ["diff"], "status": ["status", "--porcelain"],
    }.items()} == before
    assert (path / "untracked.txt").read_text() == "keep me\n"

    # Scratch cleanup keeps this evidence bound to the review attempt; a later
    # builder in the preserved checkout must not upload or inherit its baseline.
    monkeypatch.setenv("BASELINE_TEST_TASK", "build-2")
    monkeypatch.setenv("BASELINE_TEST_OUTCOME", "fixed")
    later = runner.execute({"id": "build-2", "kind": "resolve", "backend": "codex", "model": "gpt-6-astra",
                            "repo": str(origin), "branch": "hive/item", "preserve_checkout": True,
                            "instructions": "Continue the dashboard."}, {}, None)
    assert not later["is_error"]
    assert "/api/tasks/build-2/artifacts/review-baseline.json" not in uploaded
    assert not (path / ".hive/artifacts/review-baseline.json").exists()
    assert "Review baseline:" not in capture.read_text()


def test_failed_baseline_fetch_stops_before_agent_without_losing_work(review_checkout, scripted_agent):
    """An unreachable origin is an explicit preparation failure; stale local
    refs are not silently substituted and the partial checkout remains intact."""
    path, origin, _, head, _ = review_checkout
    uploaded, capture = scripted_agent
    before = git(path, "status", "--porcelain"), git(path, "diff", "--cached"), git(path, "diff")
    origin.rename(origin.with_name("offline.git"))
    result = runner.execute({"id": "review-offline", "kind": "review", "backend": "codex",
                             "repo": str(origin), "branch": "hive/item", "preserve_checkout": True,
                             "instructions": "Review the dashboard."}, {}, None)
    assert result["is_error"] and "Review baseline unavailable" in result["text"]
    assert "git fetch --no-tags origin HEAD" in result["text"] and "does not appear to be a git repository" in result["text"]
    assert not capture.exists() and not uploaded
    assert git(path, "rev-parse", "HEAD") == head
    assert (git(path, "status", "--porcelain"), git(path, "diff", "--cached"), git(path, "diff")) == before
