"""Demo: the chief relocates, the worker follows — `hive.worker` standalone.

Task: your coordinator has to move to a new address (new VM, new tunnel) and
you cannot reconfigure the fleet by hand. The protocol solves it: every
register response advertises the chief's reachable URLs, workers persist them
as a roster, and after repeated errors a worker re-resolves across that
roster. So: retire the old chief, start the new one at an address the old one
advertised, and watch the same worker finish the job there.

    uv run python demos/worker/failover.py

Offline: two toy chiefs on localhost ports play "old" and "new".
"""

import subprocess
import tempfile
import threading
import time
from pathlib import Path

from toy_chief import ToyChief

from hive.worker import WorkerConfig, WorkerLoop

OLD_PORT, NEW_PORT = 8764, 8765
NEW_URL = f"http://127.0.0.1:{NEW_PORT}"


def execute(task: dict) -> dict:
    proc = subprocess.run(task["cmd"], shell=True, capture_output=True, text=True, timeout=30)
    return {"text": proc.stdout.strip(), "is_error": proc.returncode != 0}


# The old chief holds one task and advertises where a successor would live.
old = ToyChief(OLD_PORT, [{"id": "before-move", "cmd": "echo served by the OLD chief"}],
               advertised=[NEW_URL]).start()

with tempfile.TemporaryDirectory() as tmp:
    loop = WorkerLoop(
        WorkerConfig(
            urls=[old.url],  # the worker is seeded with the old address only
            state_path=Path(tmp) / "chiefs.json",
            retry_s=0.3,
            reconnect_after_failures=1,
            poll_idle_s=0.05,
        ),
        payload=lambda boot: {"name": "nomad-worker", "boot": boot},
        execute=execute,
    )
    worker = threading.Thread(target=lambda: loop.run(max_tasks=2), daemon=True)
    worker.start()

    while not old.results:
        time.sleep(0.05)
    print(f"task 1 done at {loop.current_url}: {old.results['before-move']['text']}")

    print("\n--- retiring the old chief; starting the new one at the advertised URL ---\n")
    old.stop()
    new = ToyChief(NEW_PORT, [{"id": "after-move", "cmd": "echo served by the NEW chief"}]).start()

    worker.join(timeout=30)
    new.stop()

    print(f"task 2 done at {loop.current_url}: {new.results['after-move']['text']}")
    print(f"\nroster after the move (preferred first): {loop.roster.candidates()}")
    assert loop.current_url == NEW_URL and "NEW" in new.results["after-move"]["text"]
print("\nthe worker followed the chief without any reconfiguration.")
