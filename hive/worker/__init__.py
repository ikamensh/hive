"""Worker lifecycle — usable on its own, no other hive imports.

The client side of a hive-style work protocol: a long-lived worker process
that finds its chief (`ChiefRoster` — an ordered, persisted candidate list,
so a relocated chief only has to be advertised), registers, heartbeats,
long-polls for task dicts, runs an injected `execute`, and delivers results
with hard retries (`WorkerLoop`). What the worker *can do* and what a task
*means* are entirely the caller's: `payload` builds the register body,
`execute(task) -> result`.

Any process that speaks three endpoints can be a chief — see
`demos/worker/` for a ~40-line toy chief and a live chief-relocation drill.
"""

from hive.worker.loop import WorkerConfig, WorkerLoop
from hive.worker.roster import ChiefRoster, parse_urls
from hive.worker.update import update_available

__all__ = [
    "ChiefRoster",
    "WorkerConfig",
    "WorkerLoop",
    "parse_urls",
    "update_available",
]
