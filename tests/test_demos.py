"""Every offline-safe demo runs end-to-end, as a subprocess, the way a user
would run it.

Demos are the executable documentation of the isolated packages' APIs, so a
demo that crashes is an API regression, not a docs problem. Demos carry their
own assertions; this only needs exit code 0. Excluded: demos/agents/one_shot.py
(spends real agent quota) — its machinery is covered by tests/test_agents_run.py.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

OFFLINE_DEMOS = [
    "demos/fleet/identify.py",
    "demos/fleet/liveness_timeline.py",
    "demos/agents/survey.py",
    "demos/persistence/notes_store.py",
    "demos/persistence/leader_election.py",
    "demos/llm/calculator_agent.py",
    "demos/llm/custom_adapter.py",
    "demos/worker/echo_worker.py",
    "demos/worker/failover.py",
]


@pytest.mark.parametrize("demo", OFFLINE_DEMOS)
def test_demo_runs_offline(demo):
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GEMINI_API_KEY")}
    proc = subprocess.run(
        [sys.executable, str(ROOT / demo)],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=ROOT,
    )
    assert proc.returncode == 0, f"{demo} failed:\n{proc.stdout}\n{proc.stderr}"
