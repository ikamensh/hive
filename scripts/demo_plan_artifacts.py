"""Exercise the agent handoff with a local chief and a disposable Git remote.

Run: uv run python scripts/demo_plan_artifacts.py --output-dir /tmp/hive-toy
Add --backend codex (or opencode) to use a real installed coding agent. The
default scripts only the coding provider; checkout, API, durable storage,
validation, artifact upload, and Git landing all execute normally. The chief's
HTTP transport runs in process and is recreated after submission. No production
chief or GitHub repository is used.
"""

import argparse
from contextlib import ExitStack, redirect_stdout
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import hive.api as api
from hive.agents import PROBE_MARKER
from hive.cli import build_parser, run, wait_for_plan
from hive.config.settings import Config
from hive._control.supervisor import Supervisor
from hive._workstreams.plan_document import render_plan_document
from hive.models import Plan, Project, Task, TaskStatus
from hive.persistence.blobstore import LocalBlobStore
from hive.persistence.store import FileStore
from hive.runner import _daemon as daemon


TESTS = '''import csv
import io
import unittest
from export import export_csv


class ExportTests(unittest.TestCase):
    def test_headers_quoting_unicode_and_embedded_newlines(self):
        rows = [{"name": "Zoë", "note": 'comma, quote" and\\nnewline'}]
        self.assertEqual(list(csv.reader(io.StringIO(export_csv(rows)))),
                         [["name", "note"], ["Zoë", 'comma, quote" and\\nnewline']])

    def test_empty_input(self):
        self.assertEqual(export_csv([]), "")
'''

IMPLEMENTATION = '''"""Export ordered dictionaries using standard CSV escaping."""
import csv
import io


def export_csv(rows):
    if not rows:
        return ""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()
'''


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True,
                          text=True, timeout=30).stdout.strip()


def cli(client, *argv):
    args = build_parser().parse_args(argv)
    return wait_for_plan(args, client) if args.command == "plan-wait" else run(args, client)


def checks(path: Path):
    return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-v"],
                          cwd=path, capture_output=True, text=True, timeout=30)


def scripted_agent(_backend, _instructions, project_dir, *, agent_name, task_id, **_kwargs):
    if agent_name == "resolve":
        (project_dir / "export.py").write_text(IMPLEMENTATION)
        tested = checks(project_dir)
        assert tested.returncode == 0, tested.stderr
        git(project_dir, "add", "export.py")
        git(project_dir, "commit", "-m", "Implement CSV export")
        git(project_dir, "push", "origin", "HEAD")
        outcome, text = "fixed", "CSV export implemented and acceptance checks passed.\nOUTCOME: FIXED"
    else:
        assert agent_name == "review"
        baseline = json.loads((project_dir / ".hive/artifacts/review-baseline.json").read_text())
        diff = git(project_dir, "diff", baseline["base_sha"], "HEAD", "--", "export.py")
        assert "DictWriter" in diff and checks(project_dir).returncode == 0
        outcome, text = "accept", "Reviewed CSV escaping and independently reran acceptance checks.\nREVIEW: ACCEPT"
    return SimpleNamespace(
        text=text, is_error=False, cost_usd=0, input_tokens=0, output_tokens=0,
        structured_result={"task_id": task_id, "outcome": outcome, "summary": text},
        structured_result_error="", session_handle="", incomplete_reason="",
    )


def demo(output_dir: Path, backend: str = "scripted") -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    remote, seed = output_dir / "remote.git", output_dir / "seed"
    git(output_dir, "init", "--bare", "-b", "main", str(remote))
    git(output_dir, "clone", str(remote), str(seed))
    (seed / "export.py").write_text("def export_csv(rows):\n    raise NotImplementedError\n")
    (seed / "test_export.py").write_text(TESTS)
    (seed / ".gitignore").write_text("__pycache__/\n.hive/\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Seed CSV export acceptance case")
    git(seed, "push", "origin", "main")
    initial = checks(seed)
    assert initial.returncode != 0 and "NotImplementedError" in initial.stderr
    (output_dir / "initial-checks.txt").write_text(initial.stdout + initial.stderr)

    source = output_dir / "author-plan.toml"
    source.write_text(render_plan_document({
        "goal": "Users can export records as CSV with correct quoting and Unicode.",
        "context_documents": {"decisions.md": "Use Python's standard library.\nNo third-party dependencies.\n"},
        "repo_baselines": {str(remote): git(seed, "rev-parse", "HEAD")},
        "items": [{
            "key": "csv-export", "title": "Implement CSV export", "repo": str(remote),
            "story": "A user can export rows while preserving headers, quotes, commas and Unicode.",
            "constraints": "Use the standard library. Do not modify test_export.py.",
            "notes": "Implement export_csv(rows) in export.py. Return an empty string for no rows. "
                     "Run the existing unittest acceptance checks, commit and push your task branch.",
        }],
    }))
    source_text = source.read_text()
    config = Config(gcp_project="", gcs_bucket="", gh_token="", gemini_api_key="", orch_model="",
                    runner_token="toy-token", data_dir=output_dir / "chief", storage_mode="local")
    headers = {"X-Hive-Token": config.runner_token}

    def connect():
        store = FileStore(output_dir / "chief" / "store")
        supervisor = Supervisor(store, lambda _pid, _events: None)
        client = TestClient(api.create_app(store, supervisor, config,
                           blobs=LocalBlobStore(output_dir / "chief" / "blobs")))
        return client, store, supervisor

    merger = output_dir / "merger"

    def merge(repo, head, _token, message=""):
        assert repo == str(remote)
        if not merger.exists():
            git(output_dir, "clone", repo, str(merger))
        git(merger, "fetch", "origin")
        git(merger, "checkout", "main")
        git(merger, "reset", "--hard", "origin/main")
        git(merger, "merge", "--ff-only", head)
        git(merger, "push", "origin", "main")
        return git(merger, "rev-parse", "HEAD")

    def delete_branch(repo, branch, _token):
        assert repo == str(remote)
        git(merger, "push", "origin", "--delete", branch)

    with ExitStack() as stack:
        stack.enter_context(patch.object(api, "merge_branch", merge))
        stack.enter_context(patch.object(api, "delete_branch", delete_branch))
        client, store, supervisor = connect()
        first = cli(client, "plan-import", "toy-csv", str(source), "--repo", str(remote),
                    "--validate", f"{shlex.quote(sys.executable)} -B -m unittest discover -v")
        plan_id = first["plan"]["id"]
        again = cli(client, "plan-import", "toy-csv", str(source))
        assert again["plan"]["id"] == plan_id and len(store.list(Plan)) == 1
        selected = "codex" if backend == "scripted" else backend
        cli(client, "set", "toy-csv", "--builder", selected, "--reviewer", selected)
        cli(client, "plan-approve", "toy-csv")
        cli(client, "plan-export", plan_id, "-o", str(output_dir / "accepted.toml"))
        client.close()
        source.unlink()  # No author checkout or input file is needed after submission.
        client, store, supervisor = connect()
        stack.callback(client.close)
        cli(client, "plan-export", plan_id, "--submitted", "-o", str(output_dir / "submitted.toml"))
        assert (output_dir / "submitted.toml").read_text() == source_text

        runner = client.post("/api/runners/register", json={"name": "toy-runner", "backends": [selected]},
                             headers=headers).raise_for_status().json()["runner_id"]
        resource = client.get("/api/resources").raise_for_status().json()["resources"][-1]
        client.post(f"/api/resources/{resource['id']}/probe").raise_for_status()
        probe = client.post(f"/api/runners/{runner}/poll", headers=headers).raise_for_status().json()["task"]
        client.post(f"/api/tasks/{probe['id']}/result", json={"text": PROBE_MARKER},
                    headers=headers).raise_for_status()

        def upload_trace(task_id, log_file, _headers, _auth):
            if log_file and Path(log_file).exists():
                client.post(f"/api/tasks/{task_id}/trace", content=Path(log_file).read_bytes(),
                            headers=headers).raise_for_status()

        def upload_artifacts(task_id, path, _headers, _auth):
            for artifact in (path / ".hive/artifacts").glob("**/*"):
                if artifact.is_file():
                    name = artifact.relative_to(path / ".hive/artifacts").as_posix()
                    client.post(f"/api/tasks/{task_id}/artifacts/{name}", content=artifact.read_bytes(),
                                headers=headers).raise_for_status()

        actual_agent = daemon.run_agent

        def provider(*args, **kwargs):
            kwargs.pop("on_session", None)  # The demo drives the in-process transport synchronously.
            return actual_agent(*args, **kwargs)

        stack.enter_context(patch.object(daemon, "run_agent", scripted_agent if backend == "scripted" else provider))
        stack.enter_context(patch.object(daemon, "WORKDIR", output_dir / "runner"))
        stack.enter_context(patch.object(daemon, "TASK_TIMEOUT_S", 300))
        stack.enter_context(patch.object(daemon, "_upload_trace", upload_trace))
        stack.enter_context(patch.object(daemon, "_upload_artifacts", upload_artifacts))
        stack.enter_context(patch.object(daemon, "refresh_usage", lambda _backend: None))
        for _ in range(6):  # Resolve/review plus bounded repair, never a background service.
            project = store.list(Project)[0]
            supervisor.dispatch(project)
            running = store.list(Task, status=TaskStatus.running, runner_id=runner)
            if not running:
                break
            task = client.post(f"/api/runners/{runner}/poll", headers=headers).raise_for_status().json()["task"]
            payload = daemon.execute(task, headers, None)
            client.post(f"/api/tasks/{task['id']}/result", json=payload, headers=headers).raise_for_status()
        client.close()
        client, store, supervisor = connect()  # Results survive a second fresh chief/client too.
        stack.callback(client.close)
        waited = cli(client, "plan-wait", plan_id, "--timeout", "1", "--json")
        cli(client, "plan-result", plan_id, "-o", str(output_dir / "result.json"))
        result = json.loads((output_dir / "result.json").read_text())
        assert result["terminal"] and result["outcome"] == "done", result
        item = result["items"][0]
        review = next(attempt for attempt in reversed(item["attempts"]) if attempt["kind"] == "review")
        tested_sha = review["validation"]["commit_sha"]
        merged_sha = git(remote, "rev-parse", "refs/heads/main")
        assert item["merged_commit_sha"] == tested_sha == merged_sha
        final = checks(merger)
        (output_dir / "final-checks.txt").write_text(final.stdout + final.stderr)
        assert final.returncode == 0 and (merger / "test_export.py").read_text() == TESTS
        summary = {
            "backend": backend, "output_dir": str(output_dir), "plan_id": plan_id,
            "outcome": result["outcome"], "chief_restarted": True,
            "repeat_submission_reused_plan": True, "initial_tests_failed": True,
            "final_tests_passed": True, "tested_commit_sha": tested_sha,
            "merged_commit_sha": merged_sha, "wait_reason": waited["reason"],
        }
        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory for all toy data")
    parser.add_argument("--backend", choices=("scripted", "codex", "opencode"), default="scripted")
    args = parser.parse_args()
    env = {"GIT_AUTHOR_NAME": "Hive toy", "GIT_AUTHOR_EMAIL": "toy@localhost",
           "GIT_COMMITTER_NAME": "Hive toy", "GIT_COMMITTER_EMAIL": "toy@localhost",
           "KODO_RUNS_DIR": str(args.output_dir.resolve() / "agent-logs")}
    with patch.dict(os.environ, env), redirect_stdout(sys.stderr):
        summary = demo(args.output_dir, args.backend)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
