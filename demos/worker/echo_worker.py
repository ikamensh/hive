"""Demo: a shell-command worker against a toy chief — `hive.worker` standalone.

Task: you have machines and a queue of shell commands; you want a worker that
finds its coordinator, pulls commands, runs them, and reports output — with
the production concerns (heartbeat, retries, re-registration) handled for you.
The whole "chief" is ~40 lines (see toy_chief.py); the worker is the same
`WorkerLoop` hive's runner daemon runs on.

    uv run python demos/worker/echo_worker.py

Offline: chief and worker talk over localhost.
"""

import subprocess
import tempfile
from pathlib import Path

from toy_chief import ToyChief

from hive.worker import WorkerConfig, WorkerLoop

TASKS = [
    {"id": "t1", "cmd": "echo hello from the hive.worker demo"},
    {"id": "t2", "cmd": "uname -sm"},
    {"id": "t3", "cmd": "expr 6 \\* 7"},
]


def execute(task: dict) -> dict:
    proc = subprocess.run(task["cmd"], shell=True, capture_output=True, text=True, timeout=30)
    return {"text": proc.stdout.strip() or proc.stderr.strip(), "is_error": proc.returncode != 0}


chief = ToyChief(8763, TASKS).start()
with tempfile.TemporaryDirectory() as tmp:
    WorkerLoop(
        WorkerConfig(
            urls=[chief.url],
            state_path=Path(tmp) / "chiefs.json",
            poll_idle_s=0.05,  # the toy chief answers instantly; don't busy-poll
        ),
        payload=lambda boot: {"name": "echo-worker", "boot": boot},
        execute=execute,
    ).run(max_tasks=len(TASKS))
chief.stop()

print("\nresults the chief collected:")
for task_id, result in sorted(chief.results.items()):
    print(f"  {task_id}: {result['text']}")
assert chief.results["t3"]["text"] == "42"
