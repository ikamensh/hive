"""The documented toy workflow is executable with real local Git and checks."""

import json
from pathlib import Path
import subprocess
import sys


def test_plan_artifact_demo_delivers_verified_code_after_chief_restart(tmp_path):
    """A fresh CLI client retrieves accepted instructions and verified results."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "demo_plan_artifacts.py"
    run = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path / "toy")],
        capture_output=True, text=True, timeout=90,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    summary = json.loads(run.stdout)
    result = json.loads((tmp_path / "toy" / "result.json").read_text())

    assert summary["outcome"] == "done"
    assert summary["chief_restarted"] and summary["repeat_submission_reused_plan"]
    assert summary["initial_tests_failed"] and summary["final_tests_passed"]
    assert result["terminal"] and result["counts"]["done"] == 1
    item = result["items"][0]
    assert item["merged_commit_sha"] == summary["merged_commit_sha"]
    review = next(attempt for attempt in item["attempts"] if attempt["kind"] == "review")
    assert review["validation"]["exit_code"] == 0
    assert review["validation"]["commit_sha"] == summary["tested_commit_sha"]
    assert summary["tested_commit_sha"] == summary["merged_commit_sha"]
    assert summary["wait_reason"] == "completed"
